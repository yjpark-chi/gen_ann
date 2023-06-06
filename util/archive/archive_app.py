# archive_app.py
#
# Archive free user data
#
# Copyright (C) 2011-2021 Vas Vasiliadis
# University of Chicago
##
__author__ = 'Vas Vasiliadis <vas@uchicago.edu>'

import json
import os
import boto3
from botocore.exceptions import ClientError
import sys
import psycopg2

from flask import Flask, jsonify, request

app = Flask(__name__)
environment = 'archive_app_config.Config'
app.config.from_object(environment)

sys.path.append(app.config['HELPERS_PATH'])
import helpers as h


REGION = app.config['AWS_REGION_NAME']
DYNAMO = app.config['AWS_DYNAMODB_ANNOTATIONS_TABLE']
CNET = app.config['CNET']
S3_RESULTS_BUCKET = app.config['AWS_S3_RESULTS_BUCKET']
GLACIER_VAULT = app.config['GLACIER_VAULT']
ARCHIVE_QUEUE = app.config['AWS_ARCHIVE_QUEUE']

ANNOT = app.config['ANNOT']
AWS_SQS_WAIT_TIME = app.config['AWS_SQS_WAIT_TIME']
AWS_SQS_MAX_MESSAGES = app.config['AWS_SQS_MAX_MESSAGES']
KEY_SEP = app.config['KEY_SEP']
FILE_SEP = app.config['FILE_SEP']
VCF = app.config['VCF']

SNS = app.config['AWS_ARCHIVE_SNS_ARN']

#### CONNECT TO AWS RESOURCES ####
# Connect to SQS and get the message queue
try:
    sqs = boto3.resource('sqs', region_name=REGION)
except ClientError as e:
    app.logger.error(e)

# Check if requests queue exists, otherwise create it
try:
    queue = sqs.get_queue_by_name(QueueName=ARCHIVE_QUEUE)
except ClientError as e:
    if e.response['Error']['Code'] == 'AWS.SimpleQueueService.NonExistentQueue':
        queue = sqs.create_queue(QueueName=ARCHIVE_QUEUE)
        sns = boto3.resource('sns', region_name=REGION)
        topic = sns.Topic(SNS)
        try:
            subscription = topic.subscribe(
                Protocol='sqs',
                Endpoint=app.config['AWS_ARCHIVE_QUEUE_ARN']
            )
        except ClientError as e:
            print(e)
            print("Could not subscribe queue to sns")
    else:
        print("Could not connect to queue")

# connect to s3
try:
    # https://stackoverflow.com/questions/58131961/how-to-make-connection-s3-bucket-using-boto3-and-access-csv-file
    s3 = boto3.resource('s3', region_name=REGION)
    bucket = s3.Bucket(S3_RESULTS_BUCKET)
except ClientError as e:
    app.logger.error(e)

# connect to glacier
try:
    glacier = boto3.client('glacier', region_name=REGION)
except ClientError as e:
    app.logger.error(e)

# connect to dynamodb
try:
    dynamodb = boto3.resource('dynamodb', region_name=REGION)
    ann_table = dynamodb.Table(DYNAMO)
except ClientError as e:
    app.logger.error(e)

# connect to glacier
try: 
    glacier = boto3.client('glacier', region_name=REGION)
except ClientError as e:
    app.logger.error(e)

@app.route('/', methods=['GET'])
def home():
    return (f"This is the Archive utility: POST requests to /archive.")


@app.route('/archive', methods=['POST'])
def archive_free_user_data():

    request_data = json.loads(request.data)

    if request_data['Type'] == 'SubscriptionConfirmation':
        try:
            # connect to sns
            # https://boto3.amazonaws.com/v1/documentation/api/1.9.42/reference/services/sns.html
            sns = boto3.client('sns', region_name=REGION)
            response = sns.confirm_subscription(
                TopicArn=request_data['TopicArn'],
                Token=request_data['Token']
                )
            app.logger.info('Subscription confirmed.')
        except ClientError as e:
            return jsonify({
                "code": 500, 
                "message": e.response['Error']['Code']
                }), 200


    # get job info
    else:
        try:
            messages = queue.receive_messages(WaitTimeSeconds=AWS_SQS_WAIT_TIME, MaxNumberOfMessages=AWS_SQS_MAX_MESSAGES)
        except ClientError as e:
            return jsonify({"code": 500, "message": "Could not receive messages from queue."})
        for message in messages:
            message_info = json.loads(json.loads(message.body)['Message'])

            user = message_info.get('user_id', None)
            job_id = message_info.get('job_id', None)
            input_file = message_info['input_file']
            file_id = f"{job_id}{FILE_SEP}{input_file}"
            result_file = f"{job_id}{FILE_SEP}{input_file.replace(VCF, '')}{ANNOT}"
            key = f"{CNET}{KEY_SEP}{user}{KEY_SEP}{file_id}{KEY_SEP}{result_file}"

            ## CHECK USER ROLE ##
            try:
                profile = h.get_user_profile(id=user)
                user_role = profile['role']
            except ClientError as e:
                app.logger.info("ClientError. Could not retrieve user information.")
                user_role = None
            except psycopg2.Error as e:
                app.logger.info("Could not retrieve user information from database.")
                user_role = None


            ### IF USER IS FREE_USER, CONTINUE WITH ARCHIVAL ###
            if user_role == 'free_user':
                app.logger.info("Free user, proceed with archival.")
                app.logger.info(f"Working on job_id {job_id} for user {user}")

                # archiving an S3 object to Glacier
                # # https://stackoverflow.com/questions/41833565/s3-buckets-to-glacier-on-demand-is-it-possible-from-boto3-api
                obj = None
                for obj in bucket.objects.filter(Prefix=key):
                    try:
                        vault_response = glacier.upload_archive(vaultName=GLACIER_VAULT,body=obj.get()['Body'].read())
                        archive_id = vault_response.get('archiveId', None)
                        print('Uploaded to vault, archive id:', archive_id)
                        if archive_id:
                            update_table(job_id, archive_id)
                            delete_from_bucket(key)
                            delete_message(message)
                    except ClientError as e:
                        print("Could not archive file. Please try again")

            else:
                # ignore if premium user
                message.delete()
                if not user:
                    app.logger.info("Could not retrieve user information. Data left unarchived.")
                else:
                    app.logger.info("Premium user, abandon archival.")

    return jsonify({"code": 200})


def update_table(job_id, archive_id):
    try:
        table_response = ann_table.update_item(
            Key={
                'job_id': job_id,
                },
            UpdateExpression="set results_file_archive_id = :a",
            ExpressionAttributeValues={
                ':a': archive_id,
                },
            )
        app.logger.info('Updated table with archive_id')
    except ClientError as e:
        print('Could not update table')
        app.logger.error(e)


def delete_from_bucket(key):
    """
    Helper function that deletes object from S3 results bucket.
    """
    try:
        response = bucket.delete_objects(
            Delete={'Objects':[{'Key': key}]}
            )
        app.logger.info('File deleted from gas-results')
    except ClientError as e:
        app.logger.error(e)


def delete_message(message):
    try:
        message.delete()
        app.logger.info('Message deleted')
    except ClientError as e:
        app.logger.error(e)
        app.logger.error('Message could not be deleted')


# Run using dev server (remove if running via uWSGI)
app.run('0.0.0.0', debug=True)
### EOF