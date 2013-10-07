# Copyright European Organization for Nuclear Research (CERN)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# You may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
#
# Authors:
# - Vincent Garonne, <vincent.garonne@cern.ch>, 2012-2013
# - Cedric Serfon, <cedric.serfon@cern.ch>, 2013

'''
Reaper is a daemon to manage file deletion.
'''

import logging
import threading
import time
import traceback

from rucio.core import monitor, rse as rse_core
from rucio.core.rse_counter import get_counter
from rucio.db.constants import ReplicaState
from rucio.rse.rsemanager import RSEMgr
from rucio.common.exception import SourceNotFound
from rucio.common.config import config_get


logging.getLogger("requests").setLevel(logging.CRITICAL)

logging.basicConfig(filename='%s/%s.log' % (config_get('common', 'logdir'), __name__),
                    level=getattr(logging, config_get('common', 'loglevel').upper()),
                    format='%(asctime)s\t%(process)d\t%(levelname)s\t%(message)s')

graceful_stop = threading.Event()


def __check_rse_usage(rse, rse_id):
    """
    Internal method to check RSE usage and limits.

    :param rse_id: the rse name.
    :param rse_id: the rse id.

    :returns : max_being_deleted_files, needed_free_space, used, free.
    """
    max_being_deleted_files, needed_free_space, used, free = None, None, None, None

    # Get RSE limits
    limits = rse_core.get_rse_limits(rse=rse, rse_id=rse_id)
    if not limits and 'MinFreeSpace' not in limits and 'MaxBeingDeletedFiles' not in limits:
        return max_being_deleted_files, needed_free_space, used, free

    min_free_space = limits.get('MinFreeSpace')
    max_being_deleted_files = limits.get('MaxBeingDeletedFiles')

    # Get total space available
    usage = rse_core.get_rse_usage(rse=rse, rse_id=rse_id, source='srm')
    if not usage:
        return max_being_deleted_files, needed_free_space, used, free

    for u in usage:
        total = u['total']
        break

    # Get current used space
    cnt = get_counter(rse_id=rse_id)
    if not cnt:
        return max_being_deleted_files, needed_free_space, used, free
    used = cnt['bytes']

    # Get current amount of bytes and files waiting for deletion
    being_deleted = rse_core.get_sum_count_being_deleted(rse_id=rse_id)
    print being_deleted

    free = total - used
    needed_free_space = min_free_space - free

    return max_being_deleted_files, needed_free_space, used, free


def reaper(once=False, mode='greedy'):
    """
    Main loop to select and delete files.
    """

    logging.info('Starting reaper')

    rsemgr = RSEMgr(server_mode=True, server_mode_with_credentials=True)

    logging.info('Reaper started')

    while not graceful_stop.is_set():
        for rse in rse_core.list_rses():
            logging.info('Running on RSE %s' % (rse['rse']))
            try:
                if mode is not 'greedy':
                    max_being_deleted_files, needed_free_space, used, free = __check_rse_usage(rse=rse['rse'], rse_id=rse['id'])
                    logging.info('Space usage for RSE %(rse)s: max_being_deleted_files, needed_free_space, used, free' % rse, max_being_deleted_files, needed_free_space, used, free)
                    replicas = rse_core.list_unlocked_replicas(rse=rse['rse'], bytes=needed_free_space, limit=max_being_deleted_files)
                else:
                    replicas = rse_core.list_unlocked_replicas(rse=rse['rse'], limit=100)
                freed_space, deleted_files = 0, 0
                logging.info('Looping over replicas without locks')
                for replica in replicas:
                    logging.debug('Mark the file replica %(scope)s:%(name)s as being deleted' % replica)
                    rse_core.update_replica_state(rse=rse['rse'], scope=replica['scope'], name=replica['name'], state=ReplicaState.BEING_DELETED)
                    monitor.record_counter(counters='reaper.deletion.being_deleted',  delta=1)
                    logging.debug('Delete the file %(scope)s:%(name)s' % replica)
                    # Should delegate the deletion to a backend
                    try:
                        rsemgr.delete(rse_id=rse['rse'], lfns=[{'scope': replica['scope'], 'name': replica['name']}, ])
                        logging.debug('Remove file replica information with size %(bytes)s for file %(scope)s:%(name)s' % replica)
                        deleted_files += 1
                        rse_core.del_replica(rse=rse['rse'], scope=replica['scope'], name=replica['name'])
                        logging.debug('Delete file replica from the DB')
                        monitor.record_counter(counters='reaper.deletion.done',  delta=1)
                        freed_space += replica['bytes']
                    except SourceNotFound:
                        logging.debug('Source not found for', rse['rse'], replica['scope'], replica['name'])
                        rse_core.del_replica(rse=rse['rse'], scope=replica['scope'], name=replica['name'])
                        logging.debug('Delete file replica from the DB')
                    except NotImplementedError:
                        logging.error('Cannot delete on %s : No protocol available' % (rse['rse']))
                    except:
                        logging.critical(traceback.format_exc())
                #logging.info('RSE: %(rse)s' % rse + '#deleted files: %(deleted_files)s, Freed space: %(freed_space)s' % locals())
                logging.info('On RSE: %s : #deleted files: %i, Freed space: %i' % (rse['rse'], deleted_files, freed_space))
            except:
                logging.critical(traceback.format_exc())

        if once:
            break
        time.sleep(0.01)

    logging.info('Graceful stop requested')
    logging.info('Graceful stop done')


def stop(signum=None, frame=None):
    """
    Graceful exit.
    """
    graceful_stop.set()


def run(once=False):
    """
    Starts up the reaper threads.
    """

    if once:
        print 'main: executing one iteration only'
        reaper(once)

    else:

        print 'main: starting threads'

        threads = [threading.Thread(target=reaper), ]

        [t.start() for t in threads]

        print 'main: waiting for interrupts'

        # Interruptible joins require a timeout.
        while threads[0].is_alive():
            [t.join(timeout=3.14) for t in threads]
