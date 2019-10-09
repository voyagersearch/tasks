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
import decimal
import json
import random
import base_job
import gridfs
from utils import status
from utils import worker_utils

status_writer = status.Writer()


class ComplexEncoder(json.JSONEncoder):
    """To handle decimal types for json encoding."""
    def default(self, obj):
        if isinstance(obj, decimal.Decimal):
            return float(obj)
        # Let the base class default method raise the TypeError
        return json.JSONEncoder.default(self, obj)


def get_collections(job):
    """Return the list of collections to index."""
    collection_names = []
    collections_to_skip = job.tables_to_skip()
    collections_to_keep = job.tables_to_keep()

    if collections_to_keep == ['*']:
        collection_names = [col for col in job.db_connection.collection_names() if not col.find('system.') > -1]
    else:
        collection_names = collections_to_keep

    if collections_to_skip:
        for cs in collections_to_skip:
            [collection_names.remove(col) for col in job.db_connection.collection_names() if not col.find('system') > -1 and col == cs]

    return collection_names


def run_job(mongodb_job):
    """Worker function to index each document in each collection in the database."""
    job = mongodb_job
    job.connect_to_zmq()
    job.connect_to_database()
    collection_names = get_collections(job)
    geo = {}

    grid_fs = None
    for collection_name in collection_names:
        if job.has_gridfs:
            if collection_name.find('.files') > 0:
                grid_fs = gridfs.GridFS(job.db_connection, collection_name.split('.')[0])

        col = job.db_connection[collection_name]
        query = job.get_table_query(col)
        if query:
            documents = col.find(eval(query))
        else:
            documents = col.find()

        if documents.count() > 0:
            fields = documents[0].keys()
            field_types = dict((k, type(v)) for k, v in documents[0].iteritems())
        else:
            break

        table_id = '{0}_{1}'.format(job.location_id, collection_name)
        if not job.schema_only:
            # Index each document -- get a suitable base 10 increment for reporting percentage.
            try:
                increment = job.get_increment(documents.count())
            except ValueError:
                status_writer.send_status('No documents found in collection, {0}'.format(collection_name))
                continue
            geometry_ops = worker_utils.GeometryOps()
            generalize_value = job.generalize_value
            for i, doc in enumerate(documents):
                fields = doc.keys()
                field_types = dict((k, type(v)) for k, v in doc.iteritems())
                if grid_fs:
                    grid_out = grid_fs.get(doc['_id'])
                    if hasattr(grid_out, 'metadata'):
                        #TODO: Determine how to ingest files stored in the database.
                        #with open(r"c:\temp\{0}".format(grid_out.filename), "wb") as fp:
                            #fp.write(grid_out.read())
                        fields += grid_out.metadata.keys()
                        field_types = dict(field_types.items() + dict((k, type(v)) for k, v in grid_out.metadata.iteritems()).items())
                        values = [doc[k] for k in doc.keys() if not k == 'metadata']
                        values += grid_out.metadata.values()
                        fields.remove('metadata')
                else:
                    values = doc.values()
                entry = {}
                geo = {}
                geo_json_converter = worker_utils.GeoJSONConverter()
                if 'loc' in doc:
                    if 'type' in doc['loc']:
                        if 'bbox' in doc['loc']:
                            if job.include_wkt:
                                wkt = geo_json_converter.convert_to_wkt(doc['loc'], 3)
                                if generalize_value == 0:
                                    geo['wkt'] = wkt
                                else:
                                    geo['wkt'] = geometry_ops.generalize_geometry(wkt, generalize_value)
                            geo['xmin'] = doc['loc']['bbox'][0]
                            geo['ymin'] = doc['loc']['bbox'][1]
                            geo['xmax'] = doc['loc']['bbox'][2]
                            geo['ymax'] = doc['loc']['bbox'][3]
                        elif 'Point' in doc['loc']['type']:
                            if job.include_wkt:
                                geo['wkt'] = geo_json_converter.convert_to_wkt(doc['loc'], 3)
                            geo['lon'] = doc['loc']['coordinates'][0]
                            geo['lat'] = doc['loc']['coordinates'][1]
                        else:
                            status_writer.send_state(status.STAT_WARNING, 'No bbox information for {0}.'.format(doc['_id']))
                    elif isinstance(doc['loc'][0], float):
                        geo['lon'] = doc['loc'][0]
                        geo['lat'] = doc['loc'][1]
                    else:
                        geo['xmin'] = doc['loc'][0][0]
                        geo['xmax'] = doc['loc'][0][1]
                        geo['ymin'] = doc['loc'][1][0]
                        geo['ymax'] = doc['loc'][1][1]
                    fields.remove('loc')
                    doc.pop('loc')
                    values = doc.values()
                mapped_fields = job.map_fields(col.name, fields, field_types)
                mapped_fields = dict(zip(mapped_fields, values))
                mapped_fields['_discoveryID'] = job.discovery_id
                mapped_fields['title'] = col.name
                mapped_fields['format_type'] = 'Record'
                mapped_fields['format'] = 'application/vnd.mongodb.record'
                entry['id'] = str(doc['_id'])
                entry['location'] = job.location_id
                entry['action'] = job.action_type
                entry['entry'] = {'geo': geo, 'fields': mapped_fields}
                entry['entry']['links'] = [{'relation': 'database', 'id': table_id}]
                job.send_entry(entry)
                if (i % increment) == 0:
                    status_writer.send_percent(float(i) / documents.count(),
                                               '{0}: {1:%}'.format(collection_name, float(i)/documents.count()),
                                               'MongoDB')

        schema = {}
        schema['name'] = collection_name
        schema_columns = []
        index_info = col.index_information()
        indexes = []
        for v in index_info.values():
            indexes.append(v['key'][0][0])
        for n, t in dict(zip(fields, field_types.values())).items():
            column = {}
            props = []
            column['name'] = n
            if 'datetime' in str(t):
                column['type'] = 'datetime'
            else:
                column['type'] = str(t)
            if n in indexes:
                props.append('INDEXED')
            if 'ObjectId' in str(t) or n == '_id':
                props.append('NOTNULLABLE')
                props.append('PRIMARY KEY')
            else:
                props.append('NULLABLE')
            column['properties'] = props
            schema_columns.append(column)
        if geo:
            props = []
            if 'loc' in indexes:
                props.append('INDEXED')
            schema_columns.append({'name': 'loc', 'isGeo': True, 'properties': props})
        schema['fields'] = schema_columns

        # Add an entry for the table itself with schema.
        table_entry = {}
        mongodb_links = []
        table_entry['id'] = '{0}_{1}'.format(job.location_id, collection_name)
        table_entry['location'] = job.location_id
        table_entry['action'] = job.action_type
        table_entry['format_type'] = 'Schema'
        table_entry['relation'] = 'contains'
        table_entry['entry'] = {'fields': {'format': 'schema', '_discoveryID': job.discovery_id, 'name': collection_name, 'path': job.mongodb_client_info, 'fi_rows': int(documents.count())}}
        table_entry['entry']['fields']['schema'] = schema
        job.send_entry(table_entry)
        mongodb_links.append(table_entry)

    mongodb_entry = {}
    mongodb_properties = {}
    mongodb_entry['id'] = job.location_id + str(random.randint(0, 1000))
    mongodb_properties['name'] = job.mongodb_database
    mongodb_properties['_discoveryID'] = job.discovery_id
    mongodb_properties['format'] = "MongoDB"
    mongodb_properties['path'] = job.mongodb_client_info
    mongodb_entry['action'] = job.action_type
    mongodb_entry['location'] = job.location_id
    mongodb_entry['entry'] = {'fields': mongodb_properties}
    mongodb_entry['entry']['links'] = mongodb_links
    job.send_entry(mongodb_entry)
