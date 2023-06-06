# archive_app_config.py
#
# Copyright (C) 2011-2022 Vas Vasiliadis
# University of Chicago
#
# Set app configuration options for archive utility
#
##
__author__ = 'Vas Vasiliadis <vas@uchicago.edu>'

class Config(object):

    CSRF_ENABLED = True

    AWS_REGION_NAME = "us-east-1"

    AWS_RESULTS_QUEUE = "yjp_a17_job_results"

    AWS_REQUESTS_QUEUE = "yjp-a17-job-requests"

    # AWS DynamoDB table
    AWS_DYNAMODB_ANNOTATIONS_TABLE = "yjp_annotations"

    CNET = "yjp"

    AWS_S3_RESULTS_BUCKET = "gas-results"

    GLACIER_VAULT = "ucmpcs"

    AWS_ARCHIVE_QUEUE = "yjp-a17-archive"

    AWS_ARCHIVE_SNS_ARN = "arn:aws:sns:us-east-1:127134666975:yjp-a17-archive"

    AWS_ARCHIVE_QUEUE_ARN = "arn:aws:sqs:us-east-1:127134666975:yjp-a17-archive"

    TO_VAULT_DIR = "/home/ubuntu/gas/archive/vault"

    ANNOT = ".annot.vcf"

    AWS_SQS_WAIT_TIME = 20
    
    AWS_SQS_MAX_MESSAGES = 10

    HELPERS_PATH = "/home/ubuntu/gas/util/"

    KEY_SEP = "/"

    FILE_SEP = "~"

    VCF = ".vcf"

### EOF