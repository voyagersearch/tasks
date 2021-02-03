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
import glob
import shutil
import xml.dom.minidom as DOM
import requests
from utils import status
from utils import task_utils
import warnings
from requests.packages.urllib3.exceptions import InsecureRequestWarning
warnings.simplefilter('ignore', InsecureRequestWarning)


# Get SSL trust setting.
verify_ssl = task_utils.get_ssl_mode()

status_writer = status.Writer()
import arcpy


class AGOLHandler(object):
    """ArcGIS Online handler class."""
    def __init__(self, portal_url, username, password, service_name):
        self.username = username
        self.password = password
        self.service_name = service_name
        self.portal_url = portal_url
        self.token, self.http = self.get_token()

    def get_token(self):
        """Generates a token."""
        query_dict = {'username': self.username,
                      'password': self.password,
                      'referer': self.portal_url}
        url = "{0}/sharing/rest/generateToken".format(self.portal_url)
        response = requests.post(url + "?f=json", data=query_dict, verify=verify_ssl)
        token = response.json()
        if "token" not in token:
            raise task_utils.PublishException(token['error']['message'] + ', ' + token['error']['details'][0])
        else:
            http_prefix = "{0}/sharing/rest".format(self.portal_url)
            return token['token'], http_prefix

    def publish(self, sd_file_name, tags, description):
        """Uploads and publishes the staging file to ArcGIS Online.
        This method uses 3rd party module: requests.
        """
        update_url = '{0}/content/users/{1}/addItem'.format(self.http, self.username)
        sd_file = {"file": open(sd_file_name, 'rb')}
        url = update_url + "?f=json&token="+self.token + \
            "&filename="+sd_file_name + \
            "&type=Service Definition"\
            "&title="+self.service_name + \
            "&tags="+tags + \
            "&description=" + description
        response = requests.post(url, files=sd_file, verify=verify_ssl)
        items = response.json()
        if "success" in items:
            publish_url = '{0}/content/users/{1}/publish'.format(self.http, self.username)
            query_dict = {'itemID': items['id'], 'filetype': 'serviceDefinition', 'f': 'json', 'token': self.token}
            json_response = requests.post(publish_url, data=query_dict, verify=verify_ssl)
            json_output = json_response.json()
            word_test = ["success", "results", "services", "notSharedWith"]
            if not any(word in json_output for word in word_test):
                raise task_utils.PublishException('Failed to publish: {0}'.format(json_output))
        else:
            raise requests.RequestException("sd file not uploaded. Errors: {0}.\n".format(items))

        return json_output['services'][0]['serviceurl']


def update_sddraft(draft_file):
    draft_parts = os.path.splitext(draft_file)
    new_draft = draft_parts[0] + '_new' + draft_parts[1]

    # Read the contents of the original SDDraft into an xml parser
    doc = DOM.parse(draft_file)

    # The follow 5 code pieces modify the SDDraft from a new MapService
    # with caching capabilities to a FeatureService with Query,Create,
    # Update,Delete,Uploads,Editing capabilities. The first two code
    # pieces handle overwriting an existing service. The last three pieces
    # change Map to Feature Service, disable caching and set appropriate
    # capabilities. You can customize the capabilities by removing items.
    # Note you cannot disable Query from a Feature Service.
    tagsType = doc.getElementsByTagName('Type')
    for tagType in tagsType:
        if tagType.parentNode.tagName == 'SVCManifest':
            if tagType.hasChildNodes():
                tagType.firstChild.data = "esriServiceDefinitionType_Replacement"

    tagsState = doc.getElementsByTagName('State')
    for tagState in tagsState:
        if tagState.parentNode.tagName == 'SVCManifest':
            if tagState.hasChildNodes():
                tagState.firstChild.data = "esriSDState_Published"

    # Change service type from map service to feature service
    typeNames = doc.getElementsByTagName('TypeName')
    for typeName in typeNames:
        # TODO: Is this a bug? It's documented as supported.
        # if typeName.firstChild.data == "{}".format('MapServer'):
        #     typeName.parentNode.getElementsByTagName("Enabled")[0].firstChild.data = "true"
        # if typeName.firstChild.data == "{}".format('FeatureServer'):
        #     typeName.parentNode.getElementsByTagName("Enabled")[0].firstChild.data = "true"
        if typeName.firstChild.data == "MapServer":
            typeName.firstChild.data = "FeatureServer"

    # Turn off caching
    configProps = doc.getElementsByTagName('ConfigurationProperties')[0]
    propArray = configProps.firstChild
    propSets = propArray.childNodes
    for propSet in propSets:
        keyValues = propSet.childNodes
        for keyValue in keyValues:
            if keyValue.tagName == 'Key':
                if keyValue.firstChild.data == "isCached":
                    keyValue.nextSibling.firstChild.data = "false"

    # Turn on feature access capabilities
    configProps = doc.getElementsByTagName('Info')[0]
    propArray = configProps.firstChild
    propSets = propArray.childNodes
    for propSet in propSets:
        keyValues = propSet.childNodes
        for keyValue in keyValues:
            if keyValue.tagName == 'Key':
                if keyValue.firstChild.data == "WebCapabilities":
                    keyValue.nextSibling.firstChild.data = "Query,Create,Update,Delete,Uploads,Editing"

    # Write the new draft to disk
    f = open(new_draft, 'w')
    doc.writexml(f)
    f.close()
    return new_draft


def create_service(temp_folder, map_document, portal_url, username, password, service_name, folder_name=''):
    """Creates a map service on an ArcGIS Server machine or in an ArcGIS Online account.

    :param temp_folder: folder path where temporary files are created
    :param map_document: map document object
    :param portal_url: the ArcGIS Online or Portal for ArcGIS URL
    :param username: username for ArcGIS Online
    :param password: password for ArcGIS Online
    :param service_name: the name of the service to be created
    :param folder_name: the name of the folder where the service is created (optional)
    """
    # Create a temporary definition file.
    draft_file = '{0}.sddraft'.format(os.path.join(temp_folder, service_name))
    status_writer.send_status(_('Creating map sd draft...'))
    arcpy.mapping.CreateMapSDDraft(map_document,
                                   draft_file,
                                   service_name,
                                   'MY_HOSTED_SERVICES',
                                   folder_name=folder_name,
                                   copy_data_to_server=True,
                                   summary=map_document.description,
                                   tags=map_document.tags)

    feature_draft_file = update_sddraft(draft_file)

    # Analyze the draft file for any errors before staging.
    status_writer.send_status(_('Analyzing the map sd draft...'))
    analysis = arcpy.mapping.AnalyzeForSD(feature_draft_file)
    if analysis['errors'] == {}:
        # Stage the service.
        stage_file = draft_file.replace('sddraft', 'sd')
        status_writer.send_status(_('Staging the map service...'))
        arcpy.StageService_server(feature_draft_file, stage_file)
    else:
        # Analyze the draft file for any errors before staging.
        analysis = arcpy.mapping.AnalyzeForSD(draft_file)
        if analysis['errors'] == {}:
            # Stage the service.
            stage_file = draft_file.replace('sddraft', 'sd')
            status_writer.send_status(_('Staging the map service...'))
            arcpy.StageService_server(draft_file, stage_file)
        # If the sddraft analysis contained errors, display them and quit.
        else:
            errors = analysis['errors']
            raise task_utils.AnalyzeServiceException(errors)

    # Upload/publish the service.
    status_writer.send_status(_('Publishing the map service to: {0}...').format(portal_url))
    agol_handler = AGOLHandler(portal_url, username, password, service_name)
    map_service_url = agol_handler.publish(stage_file, map_document.description, map_document.tags)
    status_writer.send_status(_('Successfully created: {0}').format(map_service_url))


def execute(request):
    """Deletes files.
    :param request: json as a dict
    """
    errors_reasons = {}
    errors = 0
    published = 0
    app_folder = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parameters = request['params']
    num_results, response_index = task_utils.get_result_count(parameters)
    input_items = task_utils.get_input_items(parameters[response_index]['response']['docs'])
    if num_results > task_utils.CHUNK_SIZE:
        status_writer.send_state(status.STAT_FAILED, 'Reduce results to 25 or less.')
        return
    url = task_utils.get_parameter_value(parameters, 'url', 'value')
    username = task_utils.get_parameter_value(parameters, 'username', 'value')
    password = task_utils.get_parameter_value(parameters, 'password', 'value')
    service_name = task_utils.get_parameter_value(parameters, 'service_name', 'value')
    folder_name = task_utils.get_parameter_value(parameters, 'folder_name', 'value')

    request_folder = os.path.join(request['folder'], 'temp')
    if not os.path.exists(request_folder):
        os.makedirs(request_folder)

    map_template = os.path.join(request_folder, 'output.mxd')
    shutil.copyfile(os.path.join(app_folder, 'supportfiles', 'MapTemplate.mxd'), map_template)

    for item in input_items:
        try:
            # Code required because of an Esri bug - cannot describe a map package (raises IOError).
            if item.endswith('.mpk'):
                status_writer.send_status(_('Extracting: {0}').format(item))
                arcpy.ExtractPackage_management(item, request_folder)
                pkg_folder = os.path.join(request_folder, glob.glob1(request_folder, 'v*')[0])
                mxd_file = os.path.join(pkg_folder, glob.glob1(pkg_folder, '*.mxd')[0])
                mxd = arcpy.mapping.MapDocument(mxd_file)
                create_service(request_folder, mxd, url, username, password, service_name, folder_name)
            else:
                data_type = arcpy.Describe(item).dataType
                if data_type == 'MapDocument':
                    mxd = arcpy.mapping.MapDocument(item)
                    create_service(request_folder, mxd, url, username, password, service_name, folder_name)
                elif data_type == 'Layer':
                    if item.endswith('.lpk'):
                        status_writer.send_status(_('Extracting: {0}').format(item))
                        arcpy.ExtractPackage_management(item, request_folder)
                        pkg_folder = os.path.join(request_folder, glob.glob1(request_folder, 'v*')[0])
                        item = os.path.join(pkg_folder, glob.glob1(pkg_folder, '*.lyr')[0])
                    layer = arcpy.mapping.Layer(item)
                    mxd = arcpy.mapping.MapDocument(map_template)
                    mxd.description = layer.name
                    mxd.tags = layer.name
                    mxd.save()
                    data_frame = arcpy.mapping.ListDataFrames(mxd)[0]
                    arcpy.mapping.AddLayer(data_frame, layer)
                    mxd.save()
                    create_service(request_folder, mxd, url, username, password,  service_name, folder_name)
                elif data_type in ('FeatureClass', 'ShapeFile', 'RasterDataset'):
                    if data_type == 'RasterDataset':
                        arcpy.MakeRasterLayer_management(item, os.path.basename(item))
                    else:
                        arcpy.MakeFeatureLayer_management(item, os.path.basename(item))
                    layer = arcpy.mapping.Layer(os.path.basename(item))
                    mxd = arcpy.mapping.MapDocument(map_template)
                    mxd.description = layer.name
                    mxd.tags = layer.name
                    mxd.save()
                    data_frame = arcpy.mapping.ListDataFrames(mxd)[0]
                    arcpy.mapping.AddLayer(data_frame, layer)
                    mxd.save()
                    create_service(request_folder, mxd, url, username, password, service_name, folder_name)
                published += 1
        except task_utils.AnalyzeServiceException as ase:
            status_writer.send_state(status.STAT_FAILED, _(ase))
            errors_reasons[item] = repr(ase)
            errors += 1
        except requests.RequestException as re:
            status_writer.send_state(status.STAT_FAILED, _(re))
            errors_reasons[item] = repr(re)
            errors += 1
        except task_utils.PublishException as pe:
            status_writer.send_state(status.STAT_FAILED, _(pe))
            errors_reasons[item] = repr(pe)
            errors += 1
        except arcpy.ExecuteError as ee:
            status_writer.send_state(status.STAT_FAILED, _(ee))
            errors_reasons[item] = repr(ee)
            errors += 1
        except Exception as ex:
            status_writer.send_state(status.STAT_FAILED, _(ex))
            errors_reasons[item] = repr(ex)
            errors += 1
        finally:
            task_utils.report(os.path.join(request['folder'], '__report.json'), published, 0, errors, errors_reasons)
