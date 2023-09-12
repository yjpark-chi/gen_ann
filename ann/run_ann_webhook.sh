#!/bin/bash

# run_ann_webhook.sh
#
# Copyright (C) 2011-2022 Vas Vasiliadis
# University of Chicago
#
# Runs the annotator as a web server
#
##

# SSL with uWSGI not usable due to SNS subscription confirmation
# not working for HTTPS endpoints with letsencrypt wildcard certs
# Use plain ol' Flask dev server instead

#export SOURCE_HOST=0.0.0.0
#export HOST_PORT=4433
export ANN_APP_HOME=/home/ubuntu/gas/ann
cd $ANN_APP_HOME


cd /home/ubuntu/gas/ann
source /usr/local/bin/virtualenvwrapper.sh
source /home/ubuntu/.virtualenvs/mpcs/bin/activate
python /home/ubuntu/gas/ann/annotator_webhook.py

### EOF