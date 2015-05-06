# Copyright European Organization for Nuclear Research (CERN)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# You may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0
#
# Authors:
# - Wen Guan, <wen.guan@cern.ch>, 2015
# - Mario Lassnig, <mario.lassnig@cern.ch>, 2015

"""
Conveyor finisher is a daemon to update replicas and rules based on requests.
"""

import datetime
import logging
import os
import re
import socket
import sys
import threading
import time
import traceback

from sqlalchemy.exc import DatabaseError

from rucio.common.config import config_get
from rucio.common.exception import DatabaseException
from rucio.core import request, heartbeat
from rucio.core.monitor import record_timer, record_counter
from rucio.daemons.conveyor import common
from rucio.db.constants import RequestState, RequestType


logging.basicConfig(stream=sys.stdout,
                    level=getattr(logging, config_get('common', 'loglevel').upper()),
                    format='%(asctime)s\t%(process)d\t%(levelname)s\t%(message)s')

graceful_stop = threading.Event()


def finisher(once=False, process=0, total_processes=1, thread=0, total_threads=1, bulk=1000):
    """
    Main loop to update the replicas and rules based on finished requests.
    """

    logging.info('finisher starting - process (%i/%i) thread (%i/%i) bulk (%i)' % (process, total_processes,
                                                                                   thread, total_threads,
                                                                                   bulk))
    executable = ' '.join(sys.argv)
    hostname = socket.getfqdn()
    pid = os.getpid()
    hb_thread = threading.current_thread()

    logging.info('finisher started - process (%i/%i) thread (%i/%i) bulk (%i)' % (process, total_processes,
                                                                                  thread, total_threads,
                                                                                  bulk))

    while not graceful_stop.is_set():

        try:
            heartbeat.live(executable, hostname, pid, hb_thread)

            ts = time.time()

            logging.debug('%i:%i - start to update %s finished requests' % (process, thread, bulk))
            reqs = request.get_next(request_type=[RequestType.TRANSFER, RequestType.STAGEIN, RequestType.STAGEOUT],
                                    state=[RequestState.DONE, RequestState.FAILED, RequestState.LOST, RequestState.SUBMITTING],
                                    limit=bulk,
                                    older_than=datetime.datetime.utcnow(),
                                    process=process, total_processes=total_processes,
                                    thread=thread, total_threads=total_threads)
            record_timer('daemons.conveyor.finisher.000-get_next', (time.time()-ts)*1000)

            if reqs:
                logging.debug('%i:%i - updating %i requests' % (process, thread, len(reqs)))

            if not reqs or reqs == []:
                if once:
                    break
                logging.debug("no requests found. will sleep 60 seconds")
                time.sleep(60)  # Only sleep if there is nothing to do
                continue

            ts = time.time()
            common.handle_requests(reqs)
            record_timer('daemons.conveyor.finisher.handle_requests', (time.time()-ts)*1000/len(reqs))
            record_counter('daemons.conveyor.finisher.handle_requests', len(reqs))

            if len(reqs) < 100:
                logging.debug("not enough requests found. will sleep 60 seconds")
                time.sleep(60)
        except (DatabaseException, DatabaseError), e:
            if isinstance(e.args[0], tuple) and (re.match('.*ORA-00054.*', e.args[0][0]) or ('ERROR 1205 (HY000)' in e.args[0][0])):
                logging.warn("Lock detected when handling request - skipping: %s" % str(e))
            else:
                logging.error(traceback.format_exc())
        except:
            logging.critical(traceback.format_exc())

        if once:
            return

    logging.debug('%i:%i - graceful stop requests' % (process, thread))

    heartbeat.die(executable, hostname, pid, hb_thread)

    logging.debug('%i:%i - graceful stop done' % (process, thread))


def stop(signum=None, frame=None):
    """
    Graceful exit.
    """

    graceful_stop.set()


def run(once=False, process=0, total_processes=1, total_threads=1, bulk=1000):
    """
    Starts up the conveyer threads.
    """

    if once:
        logging.info('executing one finisher iteration only')
        finisher(once=once, bulk=bulk)

    else:

        logging.info('starting finisher threads')
        threads = [threading.Thread(target=finisher, kwargs={'process': process,
                                                             'total_processes': total_processes,
                                                             'thread': i,
                                                             'total_threads': total_threads,
                                                             'bulk': bulk}) for i in xrange(0, total_threads)]

        [t.start() for t in threads]

        logging.info('waiting for interrupts')

        # Interruptible joins require a timeout.
        while len(threads) > 0:
            [t.join(timeout=3.14) for t in threads if t and t.isAlive()]
