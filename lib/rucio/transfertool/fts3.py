# Copyright European Organization for Nuclear Research (CERN)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# You may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0
#
# Authors:
# - Mario Lassnig, <mario.lassnig@cern.ch>, 2013-2014
# - Vincent Garonne, <vincent.garonne@cern.ch>, 2013-2014
# - Wen Guan, <wen.guan@cern.ch>, 2014

import json
import logging
import sys

import requests

from rucio.common.config import config_get
from rucio.db.constants import FTSState


logging.getLogger("requests").setLevel(logging.CRITICAL)

logging.basicConfig(stream=sys.stdout,
                    level=getattr(logging, config_get('common', 'loglevel').upper()),
                    format='%(asctime)s\t%(process)d\t%(levelname)s\t%(message)s')

__CACERT = config_get('conveyor', 'cacert')
__USERCERT = config_get('conveyor', 'usercert')


def submit_transfers(transfers, job_metadata, transfer_host):
    """
    Submit a transfer to FTS3 via JSON.

    :param transfers: Dictionary containing 'request_id', 'src_urls', 'dest_urls', 'filesize', 'md5', 'adler32', 'overwrite', 'job_metadata', 'src_spacetoken', 'dest_spacetoken'
    :param job_metadata: Dictionary containing key/value pairs, for all transfers.
    :param transfer_host: FTS server as a string.
    :returns: List of FTS transfer identifiers
    """

    # Early sanity check
    for transfer in transfers:
        if not transfer['src_urls'] or transfer['src_urls'] == []:
            raise Exception('No sources defined')

    # FTS3 expects 'davs' as the scheme identifier instead of https
    new_src_urls = []
    new_dst_urls = []
    for transfer in transfers:
        for url in transfer['src_urls']:
            if url.startswith('https'):
                new_src_urls.append(':'.join(['davs'] + url.split(':')[1:]))
            else:
                new_src_urls.append(url)
        for url in transfer['dest_urls']:
            if url.startswith('https'):
                new_dst_urls.append(':'.join(['davs'] + url.split(':')[1:]))
            else:
                new_dst_urls.append(url)
    transfer['src_urls'] = new_src_urls
    transfer['dest_urls'] = new_dst_urls

    # Rewrite the checksums into FTS3 format, prefer adler32 if available
    for transfer in transfers:
        transfer['checksum'] = None
        if 'md5' in transfer.keys() and transfer['md5']:
            transfer['checksum'] = 'MD5:%s' % str(transfer['md5'])
        if 'adler32' in transfer.keys() and transfer['adler32']:
            transfer['checksum'] = 'ADLER32:%s' % str(transfer['adler32'])

    transfer_ids = {}

    job_metadata['issuer'] = 'rucio'
    job_metadata['previous_attempt_id'] = None

    # we have to loop until we get proper fts3 bulk submission
    for transfer in transfers:

        job_metadata['request_id'] = transfer['request_id']

        if 'previous_attempt_id' in transfer.keys():
            job_metadata['previous_attempt_id'] = transfer['previous_attempt_id']

        params_dict = {'files': [{'sources': transfer['src_urls'],
                                  'destinations': transfer['dest_urls'],
                                  'metadata': {'issuer': 'rucio'},
                                  'filesize': int(transfer['filesize']),
                                  'checksum': str(transfer['checksum']),
                                  'activity': str(transfer['activity'])}],
                       'params': {'verify_checksum': True if transfer['checksum'] else False,
                                  'spacetoken': transfer['dest_spacetoken'] if transfer['dest_spacetoken'] else 'null',
                                  'copy_pin_lifetime': transfer['copy_pin_lifetime'] if transfer['copy_pin_lifetime'] else -1,
                                  'bring_online': transfer['bring_online'] if transfer['bring_online'] else None,
                                  'job_metadata': job_metadata,
                                  'source_spacetoken': transfer['src_spacetoken'] if transfer['src_spacetoken'] else None,
                                  'overwrite': transfer['overwrite']}}

        r = None
        params_str = json.dumps(params_dict)

        if transfer_host.startswith('https://'):
            r = requests.post('%s/jobs' % transfer_host,
                              verify=__CACERT,
                              cert=(__USERCERT, __USERCERT),
                              data=params_str,
                              headers={'Content-Type': 'application/json'})
        else:
            r = requests.post('%s/jobs' % transfer_host,
                              data=params_str,
                              headers={'Content-Type': 'application/json'})

        if r and r.status_code == 200:
            transfer_ids[transfer['request_id']] = str(r.json()['job_id'])
        else:
            raise Exception('Could not submit transfer: %s', r.content)

    return transfer_ids


def submit(request_id, src_urls, dest_urls,
           src_spacetoken=None, dest_spacetoken=None,
           filesize=None, md5=None, adler32=None,
           overwrite=True, job_metadata={}):
    """
    Submit a transfer to FTS3 via JSON.

    :param request_id: Request ID of the request as a string.
    :param src_urls: Source URL acceptable to transfertool as a list of strings.
    :param dest_urls: Destination URL acceptable to transfertool as a list of strings.
    :param src_spacetoken: Source spacetoken as a string - ignored for non-spacetoken-aware protocols.
    :param dest_spacetoken: Destination spacetoken as a string - ignored for non-spacetoken-aware protocols.
    :param filesize: Filesize in bytes.
    :param md5: MD5 checksum as a string.
    :param adler32: ADLER32 checksum as a string.
    :param overwrite: Overwrite potentially existing destination, True or False.
    :param job_metadata: Optional job metadata as a dictionary.
    :returns: FTS transfer identifier as string.
    """

    return submit_transfers(transfers={'request_id': request_id,
                                       'src_urls': src_urls,
                                       'dest_urls': dest_urls,
                                       'filesize': filesize,
                                       'md5': md5,
                                       'adler32': adler32,
                                       'overwrite': overwrite,
                                       'src_spacetoken': src_spacetoken,
                                       'dest_spacetoken': dest_spacetoken},
                            job_metadata=job_metadata)[0]


def query(transfer_id, transfer_host):
    """
    Query the status of a transfer in FTS3 via JSON.

    :param transfer_id: FTS transfer identifier as a string.
    :param transfer_host: FTS server as a string.
    :returns: Transfer status information as a dictionary.
    """

    job = None

    if transfer_host.startswith('https://'):
        job = requests.get('%s/jobs/%s' % (transfer_host, transfer_id),
                           verify=__CACERT,
                           cert=(__USERCERT, __USERCERT),
                           headers={'Content-Type': 'application/json'})
    else:
        job = requests.get('%s/jobs/%s' % (transfer_host, transfer_id),
                           headers={'Content-Type': 'application/json'})
    if job and job.status_code == 200:
        return job.json()

    raise Exception('Could not retrieve transfer information: %s', job.content)


def query_details(transfer_id, transfer_host):
    """
    Query the detailed status of a transfer in FTS3 via JSON.

    :param transfer_id: FTS transfer identifier as a string.
    :param transfer_host: FTS server as a string.
    :returns: Detailed transfer status information as a dictionary.
    """

    files = None

    if transfer_host.startswith('https://'):
        files = requests.get('%s/jobs/%s/files' % (transfer_host, transfer_id),
                             verify=__CACERT,
                             cert=(__USERCERT, __USERCERT),
                             headers={'Content-Type': 'application/json'})
    else:
        files = requests.get('%s/jobs/%s/files' % (transfer_host, transfer_id),
                             headers={'Content-Type': 'application/json'})
    if files and files.status_code == 200:
        return files.json()

    return


def bulk_query(transfer_ids, transfer_host):
    """
    Query the status of a bulk of transfers in FTS3 via JSON.

    :param transfer_ids: FTS transfer identifiers as a list.
    :param transfer_host: FTS server as a string.
    :returns: Transfer status information as a dictionary.
    """

    job = None

    responses = {}
    if transfer_host.startswith('https://'):
        fts_session = requests.Session()
        for transfer_id in transfer_ids:
            job = fts_session.get('%s/jobs/%s' % (transfer_host, transfer_id),
                                  verify=__CACERT,
                                  cert=(__USERCERT, __USERCERT),
                                  headers={'Content-Type': 'application/json'})
            if not job:
                responses[transfer_id] = Exception('Could not retrieve transfer information: %s' % job)
            elif job.status_code == 200:
                responses[transfer_id] = job.json()

                if responses[transfer_id]['job_state'] in (str(FTSState.FAILED),
                                                           str(FTSState.FINISHEDDIRTY),
                                                           str(FTSState.CANCELED),
                                                           str(FTSState.FINISHED)):
                    files = fts_session.get('%s/jobs/%s/files' % (transfer_host, transfer_id),
                                            verify=__CACERT,
                                            cert=(__USERCERT, __USERCERT),
                                            headers={'Content-Type': 'application/json'})
                    if files and files.status_code == 200:
                        responses[transfer_id]['files'] = files.json()
                    else:
                        responses[transfer_id]['files'] = Exception('Could not retrieve files information: %s', files)

            elif "No job with the id" in job.text:
                responses[transfer_id] = None
            else:
                responses[transfer_id] = Exception('Could not retrieve transfer information: %s', job.content)
    else:
        fts_session = requests.Session()
        for transfer_id in transfer_ids:
            job = requests.get('%s/jobs/%s' % (transfer_host, transfer_id),
                               headers={'Content-Type': 'application/json'})
            if not job:
                responses[transfer_id] = Exception('Could not retrieve transfer information: %s' % job)
            elif job.status_code == 200:
                responses[transfer_id] = job.json()
                if responses[transfer_id]['job_state'] in (str(FTSState.FAILED),
                                                           str(FTSState.FINISHEDDIRTY),
                                                           str(FTSState.CANCELED),
                                                           str(FTSState.FINISHED)):
                    files = requests.get('%s/jobs/%s/files' % (transfer_host, transfer_id),
                                         headers={'Content-Type': 'application/json'})
                    if files and files.status_code == 200:
                        responses[transfer_id]['files'] = files.json()
                    else:
                        responses[transfer_id]['files'] = Exception('Could not retrieve files information: %s', files)

            elif "No job with the id" in job.text:
                responses[transfer_id] = None
            else:
                responses[transfer_id] = Exception('Could not retrieve transfer information: %s', job.content)

    return responses


def cancel(transfer_id, transfer_host):
    """
    Cancel a transfer that has been submitted to FTS via JSON.

    :param transfer_id: FTS transfer identifier as a string.
    :param transfer_host: FTS server as a string.
    """

    job = None

    if transfer_host.startswith('https://'):
        job = requests.delete('%s/jobs/%s' % (transfer_host, transfer_id),
                              verify=__CACERT,
                              cert=(__USERCERT, __USERCERT),
                              headers={'Content-Type': 'application/json'})
    else:
        job = requests.delete('%s/jobs/%s' % (transfer_host, transfer_id),
                              headers={'Content-Type': 'application/json'})
    if job and job.status_code == 200:
        return job.json()

    raise Exception('Could not cancel transfer: %s', job.content)


def whoami(transfer_host):
    """
    Returns credential information from the FTS3 server.

    :param transfer_host: FTS server as a string.

    :returns: Credentials as stored by the FTS3 server as a dictionary.
    """

    r = None

    if transfer_host.startswith('https://'):
        r = requests.get('%s/whoami' % transfer_host,
                         verify=__CACERT,
                         cert=(__USERCERT, __USERCERT),
                         headers={'Content-Type': 'application/json'})
    else:
        r = requests.get('%s/whoami' % transfer_host,
                         headers={'Content-Type': 'application/json'})

    if r and r.status_code == 200:
        return r.json()

    raise Exception('Could not retrieve credentials: %s', r.content)


def version(transfer_host):
    """
    Returns FTS3 server information.

    :param transfer_host: FTS server as a string.

    :returns: FTS3 server information as a dictionary.
    """

    r = None

    if transfer_host.startswith('https://'):
        r = requests.get('%s/' % transfer_host,
                         verify=__CACERT,
                         cert=(__USERCERT, __USERCERT),
                         headers={'Content-Type': 'application/json'})
    else:
        r = requests.get('%s/' % transfer_host,
                         headers={'Content-Type': 'application/json'})

    if r and r.status_code == 200:
        return r.json()

    raise Exception('Could not retrieve version: %s', r.content)
