#!/bin/bash

# run_thaw_app.sh
#
# Copyright (C) 2011-2021 Vas Vasiliadis
# University of Chicago
#
# Runs the Glacier thawing utility Flask app
#
##

# Certain variables have been removed for privacy.
SSL_CERT_PATH=""
SSL_KEY_PATH=""

cd /home/ubuntu/gas

export ARCHIVE_APP_HOME=/home/ubuntu/gas/util/thaw
export SOURCE_HOST=0.0.0.0
export HOST_PORT=4433

/home/ubuntu/.virtualenvs/mpcs/bin/uwsgi \
  --manage-script-name \
  --enable-threads \
  --vacuum \
  --log-master \
  --chdir $ARCHIVE_APP_HOME \
  --socket /tmp/thaw_app.sock \
  --mount /thaw_app=thaw_app:app \
  --http $SOURCE_HOST:$HOST_PORT,$SSL_CERT_PATH,$SSL_KEY_PATH

### EOF