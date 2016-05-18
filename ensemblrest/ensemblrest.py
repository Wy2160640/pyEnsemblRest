"""

    This file is part of pyEnsemblRest.
    Copyright (C) 2013-2016, Steve Moss

    pyEnsemblRest is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    pyEnsemblRest is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with pyEnsemblRest.  If not, see <http://www.gnu.org/licenses/>.

    EnsemblRest is a library for Python that wrap the EnsEMBL REST API.
    It simplifies all the API endpoints by abstracting them away from the
    end user and thus also ensures that an amendments to the library and/or
    EnsEMBL REST API won't cause major problems to the end user.

    Any questions, comments or issues can be addressed to gawbul@gmail.com.
    
"""

# import system modules
import re
import json
import time
import logging
import requests

# import ensemblrest modules
from . import __version__
from .ensembl_config import ensembl_default_url, ensembl_genomes_url, ensembl_api_table, ensembl_http_status_codes, ensembl_user_agent, ensembl_content_type
from .exceptions import EnsemblRestError, EnsemblRestRateLimitError, EnsemblRestServiceUnavailable

# Logger instance
logger = logging.getLogger(__name__)

# EnsEMBL REST API object
class EnsemblRest(object):
    # class initialisation function
    def __init__(self, **kwargs):
        # read args variable into object as session_args
        self.session_args = kwargs or {}
        
        #In order to rate limiting the requests, like https://github.com/Ensembl/ensembl-rest/wiki/Example-Python-Client
        self.reqs_per_sec = 15
        self.req_count = 0
        self.last_req = 0
        
        # initialise default values
        default_base_url = ensembl_default_url
        default_headers = ensembl_user_agent
        default_content_type = ensembl_content_type
        default_proxies = {}
        
        # set default values if not client arguments
        if 'base_url' not in self.session_args:
            self.session_args['base_url'] = default_base_url
        if 'headers' not in self.session_args:
            self.session_args['headers'] = default_headers
        elif 'User-Agent' not in self.session_args['headers']:
            self.session_args['headers'].update(default_headers)
        elif 'Content-Type' not in self.session_args['headers']:
            self.session_args['headers'].update(default_content_type)
        if 'proxies' not in self.session_args:
            self.session_args['proxies'] = default_proxies
        
        # setup requests session
        self.session = requests.Session()
        
        # update requests client with arguments
        client_args_copy = self.session_args.copy()
        for key, val in client_args_copy.items():
            if key in ('base_url', 'proxies'):
                setattr(self.session, key, val)
                self.session_args.pop(key)
        
        # update headers as already exist within client
        self.session.headers.update(self.session_args.pop('headers'))

        # iterate over ensembl_api_table keys and add key to class namespace
        for fun_name in ensembl_api_table.keys():
            #setattr(self, key, self.register_api_func(key))
            #Not as a class attribute, but a class method
            self.__dict__[fun_name] = self.register_api_func(fun_name)
            
            #Set __doc__ for generic class method
            if ensembl_api_table[fun_name].has_key("doc"):
                self.__dict__[fun_name].__doc__ = ensembl_api_table[fun_name]["doc"]
            
            #add function name to the class methods
            self.__dict__[fun_name].__name__ = fun_name
            

    # dynamic api registration function
    def register_api_func(self, api_call):
        return lambda **kwargs: self.call_api_func(api_call, **kwargs)

    # dynamic api call function
    def call_api_func(self, api_call, **kwargs):
        # build url from ensembl_api_table kwargs
        func = ensembl_api_table[api_call]
        
        #Verify required variables and raise an Exception if needed
        mandatory_params = re.findall('\{\{(?P<m>[a-zA-Z_]+)\}\}', func['url'])
        
        for param in mandatory_params:
            if not kwargs.has_key(param):
                logger.critical("'%s' param not specified. Mandatory params are %s" %(param, mandatory_params))
                raise Exception, "mandatory param '%s' not specified" %(param)
        
        url = re.sub('\{\{(?P<m>[a-zA-Z_]+)\}\}', lambda m: "%s" % kwargs.get(m.group(1)), self.session.base_url + func['url'])
        
        #debug
        logger.debug("Resolved url: '%s'" %(url))
        
        #Now I have to remove mandatory params from kwargs        
        for param in mandatory_params:
            del(kwargs[param])
        
        #Evaluating the numer of request in a second (according to EnsEMBL rest specification)
        if self.req_count >= self.reqs_per_sec:
            delta = time.time() - self.last_req
            if delta < 1:
                time.sleep(1 - delta)
            self.last_req = time.time()
            self.req_count = 0
        
        #check the request type (GET or POST?)
        if func['method'] == 'GET':
            logger.debug("Submitting a GET request. url = '%s', headers = %s, params = %s" %(url, {"Content-Type": func['content_type']}, kwargs))
            resp = self.session.get(url, headers={"Content-Type": func['content_type']}, params=kwargs)
            
            
        elif func['method'] == 'POST':
            #do the request
            resp = self.session.post(url, headers={"Content-Type": func['content_type']}, data=json.dumps(kwargs))
                
        else:
            raise NotImplementedError, "Method '%s' not yet implemented" %(func['method'])
        
        #Increment the request counter to rate limit requests    
        self.req_count += 1
        
        #record response for debug intent
        self.last_response = resp
        
        # parse status codes
        if resp.status_code > 304:
            ExceptionType = EnsemblRestError
            
            #Try to derive a more useful message than ensembl default message
            if resp.status_code == 400:
                try:
                    message = json.loads(resp.text)["error"]
                    
                except KeyError, message:
                    #set the default message as ensembl default
                    message = ensembl_http_status_codes[resp.status_code][1]
                    
            else:
                #default ensembl message
                message = ensembl_http_status_codes[resp.status_code][1]
            
            
            if resp.status_code == 429:
                ExceptionType = EnsemblRestRateLimitError

            raise ExceptionType(message, error_code=resp.status_code)

        #handle content in different way relying on content-type
        if func['content_type'] == 'application/json':
            content = json.loads(resp.text)
        
        else:
            #default 
            content = resp.text
            
        return content

# EnsEMBL Genome REST API object
class EnsemblGenomeRest(EnsemblRest):
    # class initialisation function
    def __init__(self, base_url=ensembl_genomes_url, **kwargs):
        #override default base_url
        kwargs["base_url"] = base_url
        
        #Call the Base Class init method
        EnsemblRest.__init__(self, **kwargs)
    
    
#module end

