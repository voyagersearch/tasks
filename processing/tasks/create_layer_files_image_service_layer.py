# -*- coding: utf-8 -*-
# (C) Copyright 2014 Voyager Search
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import os
import sys
import json
import shutil
import requests
import arcpy
from utils import status
from utils import task_utils


status_writer = status.Writer()
result_count = 0
processed_count = 0.
skipped_reasons = {}
errors_reasons = {}
arcpy.env.overwriteOutput = True
mxd_path = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), 'supportfiles', 'GroupLayerTemplate.mxd')


class ObjectEncoder(json.JSONEncoder):
    """Support non-native Python types for JSON serialization."""
    def default(self, obj):
        if isinstance(obj, (list, dict, str, unicode, int, float, bool, type(None))):
            return json.JSONEncoder.default(self, obj)


def update_index(layer_file, item_id, location, name):
    """Update the index by re-indexng an item."""
    import zmq
    indexer = sys.argv[3].split('=')[1]
    zmq_socket = zmq.Context.instance().socket(zmq.PUSH)
    zmq_socket.connect(indexer)
    entry = {"action": "UPDATE", "id": item_id, "location": location, "entry": {"fields": {"path_to_lyr": layer_file, "name": name}}}
    zmq_socket.send_json(entry, cls=ObjectEncoder)


def execute(request):
    """Copies files to a target folder.
    :param request: json as a dict.
    """
    created = 0
    skipped = 0
    errors = 0
    global result_count
    parameters = request['params']

    if not os.path.exists(request['folder']):
        os.makedirs(request['folder'])

    meta_folder = task_utils.get_parameter_value(parameters, 'meta_data_folder', 'value')
    result_count, response_index = task_utils.get_result_count(parameters)
    # Query the index for results in groups of 25.
    query_index = task_utils.QueryIndex(parameters[response_index])
    fl = query_index.fl
    fl += ',rest_endpoint,fl_objectid'
    # query = '{0}{1}{2}'.format(sys.argv[2].split('=')[1], '/select?&wt=json', fl)
    query = '{0}{1}{2}'.format("http://localhost:8888/solr/v0", '/select?&wt=json', fl)
    fq = query_index.get_fq()
    if fq:
        groups = task_utils.grouper(range(0, result_count), task_utils.CHUNK_SIZE, '')
        query += fq
    elif 'ids' in parameters[response_index]:
        groups = task_utils.grouper(list(parameters[response_index]['ids']), task_utils.CHUNK_SIZE, '')
    else:
        groups = task_utils.grouper(range(0, result_count), task_utils.CHUNK_SIZE, '')

    status_writer.send_percent(0.0, _('Starting to process...'), 'create_layer_files')
    i = 0.
    headers = {'x-access-token': task_utils.get_security_token(request['owner'])}
    for group in groups:
        i += len(group) - group.count('')
        if fq:
            results = requests.get(query + "&rows={0}&start={1}".format(task_utils.CHUNK_SIZE, group[0]), headers=headers)
        elif 'ids' in parameters[response_index]:
            results = requests.get(query + '{0}&ids={1}'.format(fl, ','.join(group)), headers=headers)
        else:
            results = requests.get(query + "&rows={0}&start={1}".format(task_utils.CHUNK_SIZE, group[0]), headers=headers)

        docs = results.json()['response']['docs']
        # docs = eval(results.read().replace('false', 'False').replace('true', 'True'))['response']['docs']
        if not docs:
            docs = parameters[response_index]['response']['docs']
        input_items = []
        for doc in docs:
            if 'rest_endpoint' in doc and 'fl_objectid' in doc:
                input_items.append((doc['id'], doc['rest_endpoint'], doc['fl_objectid'], doc['location'], doc['name']))
            else:
                input_items.append((doc['id'], doc['rest_endpoint'], '', doc['location'], doc['name']))
        result = create_layer_file(input_items, meta_folder)
        created += result[0]
        errors += result[1]
        skipped += result[2]

    try:
        shutil.copy2(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'supportfiles', '_thumb.png'), request['folder'])
    except IOError:
        pass
    # Update state if necessary.
    if errors > 0 or skipped > 0:
        status_writer.send_state(status.STAT_WARNING, _('{0} results could not be processed').format(skipped + errors))
    task_utils.report(os.path.join(request['folder'], '__report.json'), created, skipped, errors, errors_reasons, skipped_reasons)


def create_layer_file(input_items, meta_folder, show_progress=False):
    """Creates a layer for input items in the appropriate meta folders."""
    created = 0
    skipped = 0
    errors = 0
    global processed_count

    for input_item in input_items:
        try:
            lyr = None
            id = input_item[0]
            rest_endpoint = input_item[1]
            objectid = input_item[2]
            location = input_item[3]
            name = input_item[4]
            layer_folder = os.path.join(meta_folder, id[0], id[1:4])

            # Create layer folder if it does not exist.
            if not os.path.exists(layer_folder):
                os.makedirs(layer_folder)

            if not os.path.exists(os.path.join(layer_folder, '{0}.layer.lyr'.format(id))):
                try:
                    if isinstance(rest_endpoint, list):
                        rest_endpoint = rest_endpoint[0]
                    if not objectid:
                        rest_endpoint = rest_endpoint.replace('?f=json', '?f=lyr')
                        lyr = requests.get(rest_endpoint)
                        with open(os.path.join(layer_folder, '{0}.layer.lyr'.format(id)), 'wb') as fp:
                            fp.write(lyr.content)
                    else:
                        image_layer = arcpy.MakeImageServerLayer_management(in_image_service="{0}".format(rest_endpoint),
                                                              out_imageserver_layer="{0}".format(objectid),
                                                              band_index="", mosaic_method="LOCK_RASTER",
                                                              order_field="Best", order_base_value="0",
                                                              lock_rasterid="{0}".format(objectid),
                                                              where_clause="OBJECTID = {0}".format(objectid))
                        lyr = arcpy.SaveToLayerFile_management(image_layer, os.path.join(layer_folder, '{0}.layer.lyr'.format(id)))
                except arcpy.ExecuteError:
                    errors += 1
                    status_writer.send_status(arcpy.GetMessages(2))
                    errors_reasons[objectid] = arcpy.GetMessages(2)
                    continue
                except RuntimeError as re:
                    errors += 1
                    status_writer.send_status(re.message)
                    errors_reasons[objectid] = re.message
                    continue
                except AssertionError as ae:
                    status_writer.send_status(_('FAIL: {0}. {1}').format(repr(ae), id))
            else:
                lyr = os.path.join(layer_folder, '{0}.layer.lyr'.format(id))
            created += 1

            # Update the index.
            if lyr:
                try:
                    update_index(os.path.join(layer_folder, '{0}.layer.lyr'.format(id)), id, location, name)
                except (IndexError, ImportError) as ex:
                    status_writer.send_state(status.STAT_FAILED, ex)
                processed_count += 1
                status_writer.send_percent(processed_count / result_count, _('Created: {0}').format('{0}.layer.lyr'.format(id)), 'create_layer_file_image_service_layer')
        except IOError as io_err:
            processed_count += 1
            status_writer.send_percent(processed_count / result_count, _('Skipped: {0}').format(input_item), 'create_layer_file_image_service_layer')
            status_writer.send_status(_('FAIL: {0}').format(repr(io_err)))
            errors_reasons[input_item] = repr(io_err)
            errors += 1
            pass
    return created, errors, skipped
