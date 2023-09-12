#!/bin/bash

# run_archive_app.sh
#
# Copyright (C) 2011-2022 Vas Vasiliadis
# University of Chicago
#
# Runs the archive utility as a web server
#
##

# SSL with uWSGI not usable due to SNS subscription confirmation
# not working for HTTPS endpoints with letsencrypt wildcard certs
# Use plain ol' Flask dev server instead


#export SOURCE_HOST=0.0.0.0
#export HOST_PORT=4433
export ARCHIVE_APP_HOME=/home/ubuntu/gas/util/archive
cd /home/ubuntu/gas/util/archive


source /usr/local/bin/virtualenvwrapper.sh
source /home/ubuntu/.virtualenvs/mpcs/bin/activate
python /home/ubuntu/gas/util/archive/archive_app.py

### EOF