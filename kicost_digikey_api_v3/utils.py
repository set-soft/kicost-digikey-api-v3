# -*- coding: utf-8 -*-
# GPL license
#
# Copyright (C) 2021 by Salvador E. Tropea / Instituto Nacional de Tecnologia Industrial
#
import os
import re
import logging
import pickle
import time

import kicost_digikey_api_v3
from kicost_digikey_api_v3.v3.productinformation import ManufacturerProductDetailsRequest, KeywordSearchRequest
from .exceptions import DigikeyError

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 6.2; rv:22.0) Gecko/20130405 Firefox/22.0"
includes = ["DigiKeyPartNumber","ProductUrl","QuantityAvailable","MinimumOrderQuantity","PrimaryDatasheet","ProductStatus",
            "SearchLocaleUsed","StandardPricing","Parameters","RoHsStatus","AdditionalValueFee","ProductDescription"]
includes = ','.join(includes)
# Cache TTL in minutes
cache_ttl = 24*60
# Extra options for the API
extra_ops = {}
# Cache name suffix
cache_name_suffix = 'US_en_USD_US'


def create_cache_name_suffix():
    global cache_name_suffix
    cache_name_suffix = extra_ops.get('x_digikey_locale_site', 'US')
    cache_name_suffix += '_' + extra_ops.get('x_digikey_locale_language', 'en')
    cache_name_suffix += '_' + extra_ops.get('x_digikey_locale_currency', 'USD')
    cache_name_suffix += '_' + extra_ops.get('x_digikey_locale_ship_to_country', 'US')


def get_name(prefix, name):
    return os.path.join(os.environ['DIGIKEY_STORAGE_PATH'], prefix + '_' + name.replace('/', '_') + '_' + cache_name_suffix + ".dat")


def save_results(prefix, name, results):
    with open(get_name(prefix, name), "wb") as fh:
        pickle.dump(results, fh, protocol=2)


def load_results(prefix, name):
    file = get_name(prefix, name)
    if not os.path.isfile(file):
        return None, False
    mtime = os.path.getmtime(file)
    ctime = time.time()
    dif_minutes = int((ctime-mtime)/60)
    if cache_ttl < 0 or dif_minutes <= cache_ttl:
        with open(file, "rb") as fh:
            result = pickle.loads(fh.read())
        # Valid load if we got a valid result or we have a persistent cache
        return result, result is not None or cache_ttl < 0
    # Cache expired
    return None, False


class PartSortWrapper(object):
    """ This class is used to sort the results giving more priority to entries with less MOQ, less price,
        more availability, etc. """
    def __init__(self, data):
        self.data = data
        self.min_price = data.standard_pricing[0].unit_price if len(data.standard_pricing) > 0 else -1
        if not hasattr(data, 'additional_value_fee'):
            data.additional_value_fee = 0

    def __eq__(self, other):
        return (self.data.minimum_order_quantity == other.data.minimum_order_quantity and
                self.data.quantity_available == other.data.quantity_available and
                self.data.additional_value_fee == other.data.additional_value_fee and
                self.min_price == other.min_price and
                self.data.product_status == other.data.product_status)

    def __lt__(self, other):
        if self.data.quantity_available and not other.data.quantity_available:
            return True
        if not self.data.minimum_order_quantity:
            return False
        if self.data.minimum_order_quantity < other.data.minimum_order_quantity:
            return True
        if self.min_price == -1:
            return False
        dif = self.data.additional_value_fee + self.min_price - (other.data.additional_value_fee + other.min_price)
        if dif < 0:
            return True
        if dif == 0 and self.data.product_status == 'Active' and other.data.product_status != 'Active':
            return True
        return False


class by_manf_pn(object):
    def __init__(self, manf_pn):
        self.manf_pn = manf_pn

    def search(self):
        search_request = ManufacturerProductDetailsRequest(manufacturer_product=self.manf_pn, record_count=10)
        self.api_limit = {}
        results, loaded = load_results('mpn', self.manf_pn)
        if not loaded:
            results = kicost_digikey_api_v3.manufacturer_product_details(body=search_request, api_limits=self.api_limit, **extra_ops)
            save_results('mpn', self.manf_pn, results)
        # print('************************')
        # print(results)
        # print('************************')
        if not isinstance(results, list):
            results = results.product_details
        if isinstance(results, list):
            if len(results) == 1:
                result = results[0]
            elif len(results) == 0:
                result = None
            else:
                tmp_results = [PartSortWrapper(r) for r in results]
                tmp_results.sort()
                result = tmp_results[0].data
                # print('* ' + self.manf_pn + ':')
                # for rs in tmp_results:
                #    r = rs.data
                #    print('- {} {} {} {} {}'.format(r.digi_key_part_number, r.minimum_order_quantity, r.manufacturer.value, rs.min_price, r.additional_value_fee))
            # print(result)
        return result


class by_digikey_pn(object):
    def __init__(self, dk_pn):
        self.dk_pn = dk_pn

    def search(self):
        self.api_limit = {}
        result, loaded = load_results('dpn', self.dk_pn)
        if not loaded:
            result = kicost_digikey_api_v3.product_details(self.dk_pn, api_limits=self.api_limit, includes=includes, **extra_ops)
            save_results('dpn', self.dk_pn, result)
        return result


class by_keyword(object):
    def __init__(self, keyword):
        self.keyword = keyword

    def search(self):
        search_request = KeywordSearchRequest(keywords=self.keyword, record_count=10)
        self.api_limit = {}
        result, loaded = load_results('key', self.keyword)
        if not loaded:
            result = kicost_digikey_api_v3.keyword_search(body=search_request, api_limits=self.api_limit, **extra_ops) #, includes=includes)
            save_results('key', self.keyword, result)
        results = result.products
        # print(results)
        if isinstance(results, list):
            if len(results) == 1:
                result = results[0]
            elif len(results) == 0:
                result = None
            else:
                tmp_results = [PartSortWrapper(r) for r in results]
                tmp_results.sort()
                result = tmp_results[0].data
                # print('* ' + self.keyword + ':')
                # for rs in tmp_results:
                #    r = rs.data
                #    print('- {} {} {} {} {}'.format(r.digi_key_part_number, r.minimum_order_quantity, r.manufacturer.value, rs.min_price, r.additional_value_fee))
            if result is not None:
                # The keyword search returns incomplete data, do a query using the Digi-Key code
                o = by_digikey_pn(result.digi_key_part_number)
                result = o.search()
            # print(result)
        return result


def environ_add(var, value):
    """ Adds variable var to the environment, but only if not already defined """
    if os.getenv(var) is None:
        os.environ[var] = value


def configure(id, secret, sandbox, a_cache_ttl, cache_path, api_ops, a_logger=None):
    """ Load the configuration file and check we have the needed stuff """
    if a_logger:
        global logger
        logger = a_logger
        kicost_digikey_api_v3.v3.api.set_logger(a_logger)
        kicost_digikey_api_v3.oauth.oauth2.set_logger(a_logger)
    # Ensure we have a place to store the token
    if not os.path.isdir(cache_path):
        raise DigikeyError("No directory to store tokens, please create `{}`".format(cache_path))
    os.environ['DIGIKEY_STORAGE_PATH'] = cache_path
    # Ensure we have the credentials
    if not id or not secret:
        raise DigikeyError("No Digi-Key credentials defined")
    os.environ['DIGIKEY_CLIENT_ID'] = id
    os.environ['DIGIKEY_CLIENT_SECRET'] = secret
    # Default to no sandbox
    os.environ['DIGIKEY_CLIENT_SANDBOX'] = str(sandbox)
    # Cache TTL (Time To Live)
    global cache_ttl
    cache_ttl = int(a_cache_ttl*24*60)
    # API options
    global extra_ops
    extra_ops = {'x_digikey_'+op: val for op, val in api_ops.items()}
    create_cache_name_suffix()
    # Debug information about what we got
    logger.debug('Digi-Key API plug-in options:')
    logger.debug(str([k + '=' + v for k, v in os.environ.items() if k.startswith('DIGIKEY_')]))
    logger.debug(str(extra_ops))
    logger.debug('cache suffix: ' + cache_name_suffix)
