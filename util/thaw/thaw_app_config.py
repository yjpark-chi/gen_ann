# thaw_app_config.py
#
# Copyright (C) 2011-2021 Vas Vasiliadis
# University of Chicago
#
# Set app configuration options for thaw utility
#
##
__author__ = 'Vas Vasiliadis <vas@uchicago.edu>'

import os

basedir = os.path.abspath(os.path.dirname(__file__))

class Config(object):

    CSRF_ENABLED = True

    AWS_REGION_NAME = "us-east-1"

    # AWS DynamoDB table
    AWS_DYNAMODB_ANNOTATIONS_TABLE = "yjp_annotations"

    # AWS Glacier vault
    AWS_GLACIER_VAULT = "ucmpcs"
    EXPEDITED = "Expedited"
    STANDARD = "Standard"
    ARCHIVE_JOB_TYPE = "archive-retrieval"

    # AWS Archive queue
    AWS_SQS_WAIT_TIME = 20
    AWS_SQS_MAX_MESSAGES = 10
    AWS_THAW_QUEUE = "yjp-a17-thaw" 
    AWS_THAW_SQS_ARN = "arn:aws:sqs:us-east-1:127134666975:yjp-a17-thaw"

    # AWS Results bucket
    AWS_S3_RESULTS_BUCKET = "gas-results"

    # AWS SNS
    AWS_RESTORE_SNS = "arn:aws:sns:us-east-1:127134666975:yjp-a17-restore"
    AWS_THAW_SNS_ARN = "arn:aws:sns:us-east-1:127134666975:yjp-a17-thaw"

    # general
    KEY_SEP = "/"
    FILE_SEP = "~"
    DESC_SEP = ","

### EOF
