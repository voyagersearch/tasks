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
import requests
import urllib
from utils import status
from utils import task_utils
import warnings
from requests.packages.urllib3.exceptions import InsecureRequestWarning
warnings.simplefilter('ignore', InsecureRequestWarning)


# Get SSL trust setting.
verify_ssl = task_utils.get_ssl_mode()

status_writer = status.Writer()
errors_reasons = {}


def find_between( s, first, last ):
    """Find a string between two characters."""
    try:
        start = s.index(first) + len(first)
        end = s.index(last, start)
        return s[start:end]
    except ValueError:
        return ""


def get_display_tempate_id(owner):
    try:
        voyager_server = sys.argv[2].split('=')[1].split('solr')[0][:-1]
        get_url = "{0}/api/rest/display/config/default".format(voyager_server)
        get_response = requests.get(get_url, verify=verify_ssl, headers={'Content-type': 'application/json',
                                                          'x-access-token': task_utils.get_security_token(owner)})
        if get_response.status_code == 200:
            return get_response.json()['id']
        else:
            return ''
    except requests.HTTPError:
        return ''
    except requests.exceptions.InvalidURL:
        return ''
    except requests.RequestException:
        return ''


def get_existing_saved_search_query(search_name, owner):
    """Retrieves the query from an existing saved search."""
    try:
        voyager_server = sys.argv[2].split('=')[1].split('solr')[0][:-1]
        get_url = "{0}/api/rest/display/ssearch/export".format(voyager_server)
        get_response = requests.get(get_url, verify=verify_ssl, headers={'Content-type': 'application/json',
                                                      'x-access-token': task_utils.get_security_token(owner)})
        search_query = ''
        if get_response.status_code == 200:
            saved_searches = get_response.json()['searches']
            for ss in saved_searches:
                if ss['title'] == search_name:
                    search_query = ss['path']
        return True, search_query
    except requests.HTTPError as http_error:
        return False, http_error
    except requests.exceptions.InvalidURL as url_error:
        return False, url_error
    except requests.RequestException as re:
        return False, re


def delete_saved_search(search_name, owner):
    """Deletes an existing saved search. This is used when overwriting a saved search."""
    try:
        voyager_server = sys.argv[2].split('=')[1].split('solr')[0][:-1]
        get_url = "{0}/api/rest/display/ssearch/export".format(voyager_server)
        get_response = requests.get(get_url, verify=verify_ssl, headers={'Content-type': 'application/json', 'x-access-token': task_utils.get_security_token(owner)})
        if get_response.status_code == 200:
            delete_url = ''
            saved_searches = get_response.json()['searches']
            for ss in saved_searches:
                if ss['title'] == search_name:
                    search_id = ss['id']
                    delete_url = "{0}/api/rest/display/ssearch/{1}".format(voyager_server, search_id)
                    break
            if delete_url:
                res = requests.delete(delete_url, verify=verify_ssl, headers={'Content-type': 'application/json', 'x-access-token': task_utils.get_security_token(owner)})
                if not res.status_code == 200:
                    if hasattr(res, 'content'):
                        return False, eval(res.content)['error']
                    else:
                        return False, 'Error creating saved search: {0}: {1}'.format(search_name, res.reason)
                else:
                    return True, ''
            else:
                return True, ''
        else:
            return False, eval(get_response.content)['message']
    except requests.HTTPError as http_error:
        return False, http_error
    except requests.exceptions.InvalidURL as url_error:
        return False, url_error
    except requests.RequestException as re:
        return False, re


def create_saved_search(search_name, groups, owner, query, has_q):
    """Create the saved search using Voyager API."""
    try:
        voyager_server = sys.argv[2].split('=')[1].split('solr')[0][:-1]
        url = "{0}/api/rest/display/ssearch".format(voyager_server)
        if query:
            template_id = get_display_tempate_id(owner)
            if has_q:
                if query.endswith('/'):
                    path = "/q=" + query + 'disp={0}'.format(template_id)
                else:
                    path = "/q=" + query + '/disp={0}'.format(template_id)
            else:
                if query.endswith('/'):
                    path ="/" + query + 'disp={0}'.format(template_id)
                else:
                    path = "/" + query + '/disp={0}'.format(template_id)
            query = {
                "title": str(search_name),
                "owner": str(owner['name']),
                "path": str(path),
                "share": groups,
                "overwrite": True
            }
        else:
            query = {
                "title": search_name,
                "owner": owner['name'],
                "path": "",
                "share": groups
            }
        response = requests.post(url, json.dumps(query), verify=verify_ssl, headers={'Content-type': 'application/json', 'x-access-token': task_utils.get_security_token(owner)})
        if response.status_code == 200:
            return True, 'Created save search: {0}'.format(response.json()['title'])
        else:
            if hasattr(response, 'content'):
                return False, eval(response.content)['error']
            else:
                return False, 'Error creating saved search: {0}: {1}'.format(search_name, response.reason)
    except requests.HTTPError as http_error:
        return False, http_error
    except requests.exceptions.InvalidURL as url_error:
        return False, url_error
    except requests.RequestException as re:
        return False, re


def execute(request):
    """Remove tags.
    :param request: json as a dict.
    """
    query = ''
    errors = 0
    parameters = request['params']
    archive_location = request['folder']
    if not os.path.exists(archive_location):
        os.makedirs(archive_location)

    # Parameter values
    search_action = task_utils.get_parameter_value(parameters, 'search_action', 'value')
    search_name = task_utils.get_parameter_value(parameters, 'saved_searches', 'value')
    search_name = eval(search_name[0])['text']
    groups = task_utils.get_parameter_value(parameters, 'groups', 'value')
    request_owner = request['owner']

    result_count, response_index = task_utils.get_result_count(parameters)
    fq = '/'
    if 'fq' in parameters[response_index]['query']:
        if isinstance(parameters[response_index]['query']['fq'], list):
            for q in parameters[response_index]['query']['fq']:
                if '{!tag=' in q:
                    q = q.split('}')[1]
                if ':' in q:
                    facet = q.split(':')[0]
                    value = q.split(':')[1]
                    if '(' in value:
                        value = value.replace('(', '').replace(')', '')
                    value = urllib.urlencode({'val': value.replace('"', '')})
                    value = value.split('val=')[1]
                    facet2 = 'f.{0}='.format(facet)
                    q = '{0}{1}'.format(facet2, value) #q.replace(facet + ':', facet2)
                fq += '{0}/'.format(q).replace('"', '')
        else:
            # Replace spaces with %20 & remove \\ to avoid HTTP Error 400.
            fq += '&fq={0}'.format(parameters[response_index]['query']['fq'].replace("\\", ""))
            if '{!tag=' in fq:
                fq = fq.split('}')[1]
            if ':' in fq:
                if fq.startswith('/&fq='):
                    fq = fq.replace('/&fq=', '')
                facet = fq.split(':')[0]
                value = fq.split(':')[1].replace('(', '').replace(')', '').replace('"', '')
                if 'place' not in facet:
                    value = urllib.urlencode({'val': value}).split('val=')[1]
                facet2 = 'f.{0}='.format(facet)
                if '(' in value:
                    fq = ''
                    if value.split(' '):
                        for v in  value.split(' '):
                            fq += (facet2 + v.replace('(', '').replace(')', '') + '/').replace(':', '')
                else:
                    value = urllib.urlencode({'val': value}).split('val=')[1]
                    fq = '{0}{1}'.format(facet2, value)
            if '{! place.op=' in fq:
                relop = find_between(fq, 'place.op=', '}')
                fq = fq.replace('}', '').replace('{', '')
                fq = fq.replace('! place.op={0}'.format(relop), '/place.op={0}/'.format(relop))
                fq = fq.replace('place:', 'place=')
                fq = fq.replace('&fq=', '')

    hasQ = False
    if 'q' in parameters[response_index]['query']:
        query = parameters[response_index]['query']['q']
        hasQ = True
        if fq:
            query += '/'

    if fq:
        if fq.startswith('/place'):
            query += fq.replace('"', '')
        elif '!tag' in query and 'OR' in query:
            # e.g. "path": "/q=id:(92cdd06e01761c4c d9841b2f59b8a326) OR format:(application%2Fvnd.esri.shapefile)"
            q = query.split('}')[1].replace('))/', '').replace('(', '').replace('(', '')
            q = urllib.urlencode({'val': q.split(':')[1]}).split('val=')[1]
            query = query.split(' OR ')[0] + ' OR ' + q
        else:
            if fq.startswith('f.//'):
                fq = fq.replace('f.//', '/').replace('"', '')
            if ' place.id' in fq:
                fq = fq.replace(' place.id', '/place.id').replace('"', '')
            if '{! place.op=' in fq:
                relop = find_between(fq, 'place.op=', '}')
                fq = fq.replace('}', '').replace('{', '')
                fq = fq.replace('! place.op={0}'.format(relop), '/place.op={0}/'.format(relop)).replace('"', '')
            query += fq.rstrip('/')
            query = query.replace('f./', '')
        query = query.replace('&fq=', '')

    if search_action == 'Overwrite an existing saved search':
        delete_result = delete_saved_search(search_name, request_owner)
        if not delete_result[0]:
            status_writer.send_state(status.STAT_FAILED, delete_result[1])
            return

    if query:
        result = create_saved_search(search_name, groups, request_owner, query, hasQ)
    else:
        result = create_saved_search(search_name, groups, request_owner, "", hasQ)
    if not result[0]:
        errors += 1
        errors_reasons[search_name] = result[1]

    # Update state if necessary.
    if errors > 0:
        status_writer.send_state(status.STAT_FAILED, result[1])
    else:
        status_writer.send_status(result[1])
    task_utils.report(os.path.join(request['folder'], '__report.json'), 1, 0, errors, errors_details=errors_reasons)
