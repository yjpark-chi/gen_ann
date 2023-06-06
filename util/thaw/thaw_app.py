# thaw_app.py
#
# Thaws upgraded (premium) user data
#
# Copyright (C) 2011-2021 Vas Vasiliadis
# University of Chicago
##
__author__ = 'Vas Vasiliadis <vas@uchicago.edu>'

import json
import os
import requests
import boto3
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key
import sys

from flask import Flask, jsonify, request

app = Flask(__name__)
environment = 'thaw_app_config.Config'
app.config.from_object(environment)
app.url_map.strict_slashes = False


KEY_SEP = app.config['KEY_SEP']
FILE_SEP = app.config['FILE_SEP']
DESC_SEP = app.config['DESC_SEP']

REGION = app.config['AWS_REGION_NAME']
AWS_SQS_WAIT_TIME = app.config['AWS_SQS_WAIT_TIME']
AWS_SQS_MAX_MESSAGES = app.config['AWS_SQS_MAX_MESSAGES']
THAW_SNS_ARN = app.config['AWS_THAW_SNS_ARN']


GLACIER_VAULT_NAME = app.config['AWS_GLACIER_VAULT']


### CONNECT TO THAW QUEUE #### 
THAW_QUEUE = app.config['AWS_THAW_QUEUE']
try:
    sqs = boto3.resource('sqs', region_name=REGION)
except ClientError as e:
    print(e, file=sys.stderr)

# Check if requests queue exists, otherwise create it
try:
    queue = sqs.get_queue_by_name(QueueName=THAW_QUEUE)
except ClientError as e:
    if e.response['Error']['Code'] == 'AWS.SimpleQueueService.NonExistentQueue':
        queue = sqs.create_queue(QueueName=THAW_QUEUE)
        sns = boto3.resource('sns', region_name=REGION)
        topic = sns.Topic(THAW_SNS_ARN)
        try:
            subscription = topic.subscribe(
                Protocol='sqs',
                Endpoint=app.config['AWS_THAW_SQS_ARN']
            )
        except ClientError as e:
            print(e)
            print("Could not subscribe queue to sns")
    else:
        print("Could not connect to queue")
########


#### CONNECT TO DYNAMODB TABLE ####
try:
    DYNAMODB = boto3.resource('dynamodb', region_name=app.config['AWS_REGION_NAME'])
    ANN_TABLE = DYNAMODB.Table(app.config['AWS_DYNAMODB_ANNOTATIONS_TABLE'])
except ClientError as e:
    app.logger.error(e)
    print("Could not connect to dynamodb")

########


#### CONNECT TO GLACIER ####
try:    # connect to glacier
    GLACIER = boto3.client('glacier', region_name=app.config['AWS_REGION_NAME'])
except ClientError as e:
    app.logger.error(e)
    print("Could not connect to vault.")

########

@app.route('/', methods=['GET'])
def home():
    return (f"This is the Thaw utility: POST requests to /thaw.")

@app.route('/thaw', methods=['POST'])
def thaw_premium_user_data():
    if (request.method == 'GET'):
        return jsonify({
            "code": 405, 
            "error": "Expecting SNS POST request."
        }), 405


    # below code is from annotator webhook
    # confirms subscription
    request_data = json.loads(request.data)
    request_type = request_data.get('Type', None)
    if request_type == 'SubscriptionConfirmation':
        app.logger.info("Subscription request received.")
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
        except KeyError as k:
            return jsonify({
                "code": 500, 
                "message": "Could not retrieve job details"
                }), 200

    else:
        if request_type == 'Notification':
            print('Job request received.')
            # retrieve messages from queue
            try:
                messages = queue.receive_messages(WaitTimeSeconds=AWS_SQS_WAIT_TIME, MaxNumberOfMessages=AWS_SQS_MAX_MESSAGES)
            except ClientError as e:
                return jsonify({"code": 500, "message": "Could not retrieve messages."})

            print('this is messages', messages)
            print(len(messages))
            if not messages:
                return jsonify({"code": 200}), 200
            for message in messages:
                # parse variables
                job_info = json.loads(json.loads(message.body)['Message'])
                app.logger.info('this is job info', job_info)
                user_id = job_info.get('user_id', None)
                user_role = job_info.get('user_role', None)
              
                print(f"Initiating archival for user {user_id}")
                # first, retrieve archive ids that are associated with the user
                try:
                    table_response = ANN_TABLE.query(
                        IndexName='user_id_index',
                        KeyConditionExpression=Key('user_id').eq(user_id)
                        )
                    table_response = table_response['Items']
                except ClientError as e:
                    app.logger.error(e)
                    return jsonify({"code": 500, "message": "Could not retrieve jobs for users"}), 500
                if not table_response:
                    app.logger.error("Query did not return items.")
                    return jsonify({"code": 200, "message": "User has no stored jobs."}), 200


                # initiating glacier jobs
                # https://github.com/boto/boto3/issues/2608
                for item in table_response:
                    if item.get('results_file_archive_id', None):
                        prefix = item['s3_key_input_file']
                        results = item['s3_key_result_file']
                        _, _, results = results.split(KEY_SEP)
                        prefix = f"{prefix}{KEY_SEP}{results}"
                        print('this is prefix', prefix)

                        # attempt expedited thaw
                        vault_response, exp_thaw = attempt_thaw(item['results_file_archive_id'], user_id, prefix, app.config['EXPEDITED'])
                        if not exp_thaw:
                            print("Attempting standard thaw")
                            vault_response, std_thaw = attempt_thaw(item['results_file_archive_id'], user_id, prefix, app.config['STANDARD'])

                        if not vault_response:
                            return jsonify({"code": 500, "message": "Could not fulfill archive retrival request."}), 500
                try:
                    message.delete()
                    print("Message deleted")
                except ClientError as e:
                    print("Could not delete message")
    return jsonify({"code": 200}), 200


def attempt_thaw(archive_id, user_id, prefix, tier):
    try:
        vault_response = GLACIER.initiate_job(
            vaultName=GLACIER_VAULT_NAME,
            jobParameters={
                'Type': app.config['ARCHIVE_JOB_TYPE'],
                'Description': f"{user_id}{DESC_SEP}{prefix}",
                'ArchiveId': archive_id,
                'SNSTopic': app.config['AWS_RESTORE_SNS'],
                'Tier': tier
            }
        )
        print(f"{tier} request successful")
        return vault_response, True
    except GLACIER.exceptions.InsufficientCapacityException:
        print(f"Insufficient capacity to run {tier} request.")
        return None, False
    except ClientError as e:
        print(e)
        print(f"{tier} request failed.")
        return None, False

### EOF
