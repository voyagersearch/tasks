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
import cx_Oracle
import base_job


class ComplexEncoder(json.JSONEncoder):
    """To handle decimal types for json encoding."""
    def default(self, obj):
        if isinstance(obj, decimal.Decimal):
            return float(obj)
        # Let the base class default method raise the TypeError
        return json.JSONEncoder.default(self, obj)


def global_job(args):
    """Create a global job object for multiprocessing."""
    global job
    job = args


def worker():
    """Worker function to index each row in each table in the database."""
    job.connect_to_zmq()
    job.connect_to_database()
    tables = []

    if not job.tables_to_keep == ['*']:
        for tk in job.tables_to_keep:
            [tables.append(t[0]) for t in job.db_cursor.execute("select table_name from user_tables where table_name like '{0}'".format(tk)).fetchall()]
    else:
        [tables.append(t[0]) for t in job.db_cursor.execute("select table_name from user_tables where object_flags='16391'").fetchall()]
    #cur.execute("select spatial_column FROM layers where table_name = 'FC_COUNTRIES'")

    # cur.execute("select column_name from all_tab_cols where table_name = 'FC_COUNTRIES' AND column_name like 'CNTRY%'")
    for tbl in set(tables):
        try:
            i = 0
            geo = {}
            has_shape = False
            is_point = False
            columns = []
            if not job.fields_to_keep == ['*']:
                for col in job.fields_to_keep:
                    qry = "SELECT COLUMN_NAME FROM all_tab_cols WHERE table_name = '{0}' AND column_name LIKE '{1}'".format(tbl, col)
                    [columns.append(c[0]) for c in job.execute_query(qry).fetchall()]
            else:
                job.db_cursor.execute("SELECT * FROM {0}".format(tbl))
                [columns.append(c[0]) for c in job.db_cursor.description]

            #[columns.remove(c) for c in columns if c.startswith('SYS_')]

            if 'SHAPE' in columns:
                has_shape = True
                columns.remove('SHAPE')
                schema = job.db_cursor.execute("select SHAPE  from {0}".format(tbl)).fetchone()[0].type.schema
                shape_type = job.db_cursor.execute("select {0}.ST_GEOMETRYTYPE(SHAPE) from {1}".format(schema, tbl)).fetchone()[0]
                if 'POINT' in shape_type:
                    columns.insert(0, '{0}.st_y(SHAPE)'.format(schema, tbl))
                    columns.insert(0, '{0}.st_x(SHAPE)'.format(schema, tbl))
                    is_point = True
                else:
                    columns.insert(0, '{0}.st_maxy(SHAPE)'.format(schema, tbl))
                    columns.insert(0, '{0}.st_maxx(SHAPE)'.format(schema, tbl))
                    columns.insert(0, '{0}.st_miny(SHAPE)'.format(schema, tbl))
                    columns.insert(0, '{0}.st_minx(SHAPE)'.format(schema, tbl))

            rows = job.db_cursor.execute("select {0} from {1}".format(','.join(columns), tbl)).fetchall()
            for i, row in enumerate(rows):
                entry = {}
                if has_shape:
                    if is_point:
                        geo['lon'] = row[0]
                        geo['lat'] = row[1]
                    else:
                        geo['xmin'] = row[0]
                        geo['ymin'] = row[1]
                        geo['xmax'] = row[2]
                        geo['ymax'] = row[3]

                mapped_cols = job.map_fields(tbl, columns)
                mapped_cols = dict(zip(mapped_cols, row))

                if has_shape:
                    if is_point:
                        mapped_cols.pop('meta_{0}.st_y(SHAPE)'.format(schema, tbl))
                        mapped_cols.pop('meta_{0}.st_x(SHAPE)'.format(schema, tbl))
                    else:
                        mapped_cols.pop('meta_{0}.st_maxx(SHAPE)'.format(schema, tbl))
                        mapped_cols.pop('meta_{0}.st_maxy(SHAPE)'.format(schema, tbl))
                        mapped_cols.pop('meta_{0}.st_minx(SHAPE)'.format(schema, tbl))
                        mapped_cols.pop('meta_{0}.st_miny(SHAPE)'.format(schema, tbl))

                mapped_cols['_discoveryID'] = job.discovery_id
                entry['id'] = '{0}_{1}_{2}'.format(job.location_id, tbl, i)
                entry['location'] = job.location_id
                entry['action'] = job.action_type
                entry['entry'] = {'geo': geo, 'fields': mapped_cols}
                job.send_entry(entry)
                i += 1
            print "Total", tbl, i
        except Exception:
            pass

def assign_job(job_info):
    """Connects to ZMQ, connects to the database, and assigns the job."""
    job = base_job.Job(job_info)
    global_job(job)
    worker()