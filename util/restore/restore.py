# restore.py
#
# Restores thawed data, saving objects to S3 results bucket
# NOTE: This code is for an AWS Lambda function
#
# Copyright (C) 2011-2021 Vas Vasiliadis
# University of Chicago
##

import boto3
import time
import os
import sys
import json
from botocore.exceptions import ClientError

# Define constants here; no config file is used for Lambdas

# Certain variables have been removed for privacy
QUEUE_NAME = ''
RESTORE_SQS_ARN = ""
RESTORE_SNS = ""
REGION = 'us-east-1'
VAULT_NAME = 'ucmpcs'
RESULTS_BUCKET = 'gas-results'
DESC_SEP = ","
DYNAMODB = ''
KEY_SEP = '/'
FILE_SEP = '~'

def lambda_handler(event, context):
    #print("Received event: " + json.dumps(event, indent=2))
    
    # connect to s3
    try:
        s3 = boto3.client('s3',region_name=REGION)
    except ClientError as e:
        print(e)
        
    # connect to glacier
    try:
        glacier = boto3.client('glacier', region_name=REGION)
    except ClientError as e:
        print(e)
    
    # connect to queue
    try:
        sqs = boto3.resource('sqs', region_name=REGION)
    except ClientError as e:
        print(e)
    
    try:
        queue = sqs.get_queue_by_name(QueueName=QUEUE_NAME)
    except ClientError as e:
        if e.response['Error']['Code'] == 'AWS.SimpleQueueService.NonExistentQueue':
            queue = sqs.create_queue(QueueName=QUEUE_NAME)
            sns = boto3.resource('sns', region_name=REGION)
            topic = sns.Topic(RESTORE_SNS)
            try:
                subscription = topic.subscribe(
                    Protocol='sqs',
                    Endpoint=RESTORE_SQS_ARN
                )
            except ClientError as e:
                print(e)
                print("Could not subscribe queue to sns")
        else:
            print("Could not connect to queue")

    # connect to dynamodb
    try:
        dynamo = boto3.resource('dynamodb', region_name=REGION)
        ann_table = dynamo.Table('DYNAMODB')
    except ClientError as e:
        print(e)
        print("Could not connect to dynamodb table")

    messages = queue.receive_messages(WaitTimeSeconds=20)
    
    for message in messages:
        job_info = json.loads(json.loads(message.body)['Message'])
        job_id = job_info['JobId']
        archive_id = job_info['ArchiveId']
        
        # attempt to retrieve file object from glacier
        try:
            output = glacier.get_job_output(vaultName=VAULT_NAME, jobId=job_id)
            print("Retrieved job output from thawed archive")
        except glacier.exceptions.InvalidParameterValueException as i:
            print('archival has not finished')
            print(i)
            continue
        except glacier.exceptions as e:
            print('could not retrieve job output')
            print(e)


        # retrieve job details to use when uploading to s3
        user_id, key = job_info['JobDescription'].split(DESC_SEP)

        try:
            s3.upload_fileobj(output['body'], RESULTS_BUCKET, key)
            print(f"{key} successfully restored to S3.")
            s3_upload = True
        except ClientError as e:
            print(e)
            print("Could not restore file object to S3 bucket.")
            s3_upload = False
        
        if s3_upload:
            try: # delete archive from glacier
                glacier.delete_archive(
                    vaultName=VAULT_NAME,
                    archiveId=archive_id
                        )
                print("Deleted archive from Glacier.")
            except glacier.exceptions as e:
                print("Could not delete archive from Glacier.")
                print(e)

            # update table item
            # https://stackoverflow.com/questions/44810743/dynamodb-remove-key-value-pair-from-map
            # https://stackoverflow.com/questions/51048477/how-to-update-several-attributes-of-an-item-in-dynamodb-using-boto3
            _, _, input_file, _ = key.split(KEY_SEP)
            table_job_id, _= input_file.split(FILE_SEP)
            try:
                ann_table.update_item(
                    TableName=DYNAMODB,
                    Key={"job_id": table_job_id},
                    UpdateExpression="REMOVE results_file_archive_id")
            except ClientError as e:
                print(e)

            message.delete()

    return {
        'statusCode': 200,
        'body': json.dumps('Hello from Lambda! We are restoring your files.')
    }


### EOF