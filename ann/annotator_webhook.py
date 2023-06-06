# annotator_webhook.py
#
# NOTE: This file lives on the AnnTools instance
# Modified to run as a web server that can be called by SNS to process jobs
# Run using: python annotator_webhook.py
#
# Copyright (C) 2011-2022 Vas Vasiliadis
# University of Chicago
##
__author__ = 'Vas Vasiliadis <vas@uchicago.edu>'

import requests
from flask import Flask, jsonify, request
import boto3
from subprocess import Popen
from botocore.client import Config
from botocore.exceptions import ClientError
import os
import json

app = Flask(__name__)
environment = 'ann_config.Config'
app.config.from_object(environment)

REGION = app.config['AWS_REGION_NAME']
SNS = app.config['AWS_SNS_ARN']
DYNAMO = app.config['AWS_DYNAMODB_ANNOTATIONS_TABLE']
BASE_DIR = app.config['ANNOTATOR_BASE_DIR']
JOBS_DIR = app.config['ANNOTATOR_JOBS_DIR']
RUN_PY = app.config['ANNOTATOR_RUN']

AWS_SQS_WAIT_TIME = app.config['AWS_SQS_WAIT_TIME']
AWS_SQS_MAX_MESSAGES = app.config['AWS_SQS_MAX_MESSAGES']
RUNNING = app.config['RUNNING']
PENDING = app.config['PENDING']

FILE_SEP = app.config['FILE_SEP']
KEY_SEP = app.config['KEY_SEP']

# Connect to SQS and get the message queue
QUEUE_NAME = app.config['AWS_SQS_QUEUE_NAME']

try:
    sqs = boto3.resource('sqs', region_name=REGION)
except ClientError as e:
    print(e, file=sys.stderr)

# Check if requests queue exists, otherwise create it
try:
    queue = sqs.get_queue_by_name(QueueName=QUEUE_NAME)
except ClientError as e:
    if e.response['Error']['Code'] == 'AWS.SimpleQueueService.NonExistentQueue':
        queue = sqs.create_queue(QueueName=QUEUE_NAME)
        sns = boto3.resource('sns', region_name=REGION)
        topic = sns.Topic(SNS)
        try:
            subscription = topic.subscribe(
                Protocol='sqs',
                Endpoint=app.config['AWS_SQS_QUEUE_ARN']
            )
        except ClientError as e:
            print(e)
            print("Could not subscribe queue to sns")
    else:
        print("Could not connect to queue")


'''
A13 - Replace polling with webhook in annotator

Receives request from SNS; queries job queue and processes message.
Reads request messages from SQS and runs AnnTools as a subprocess.
Updates the annotations database with the status of the request.
'''
@app.route('/', methods=['GET'])
def check_health():
    return jsonify({"code": 200, "message": "I'm alive"}), 200

@app.route('/process-job-request', methods=['GET', 'POST'])
def annotate():

    print('request', request)
    if (request.method == 'GET'):
        return jsonify({
            "code": 405, 
            "error": "Expecting SNS POST request."
        }), 405

    
    # Check message type

    # get data from flask request
    # https://stackoverflow.com/questions/10434599/get-the-data-received-in-a-flask-request
    # https://stackoverflow.com/questions/23205577/python-flask-immutablemultidict

    # Confirm SNS topic subscription confirmation
    # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/sns.html

    request_data = json.loads(request.data)
    request_type = request_data.get('Type', None)
    if request_type == 'SubscriptionConfirmation':
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

    # Process job request notification
    else:
        if request_type == 'Notification':
            app.logger.info('Job request received.')
            try:
                messages = queue.receive_messages(WaitTimeSeconds=AWS_SQS_WAIT_TIME, MaxNumberOfMessages=AWS_SQS_MAX_MESSAGES)
            except ClientError as e:
                return jsonify({"code": 500, "message": "Could not retrieve messages."})
            for message in messages:
                # parse variables
                job_info = json.loads(json.loads(message.body)['Message'])
                receipt_handle = message.receipt_handle
        
                try:
                    job_id = job_info['job_id']
                    user = job_info['user_id']
                    input_file = job_info['input_file_name']
                    bucket = app.config['AWS_S3_INPUTS_BUCKET']
                    key = job_info['s3_key_input_file']
                    file_id = f"{job_id}{FILE_SEP}{input_file}"
                    user_role = job_info['user_role']
                except KeyError as k:
                    print('Could not retrieve necessary job information.')
                    return jsonify({"code": 500, "message": "Could not retrieve messages."})

                # create dirs to run annotation
                create_dirs(user, file_id)

                # connect to s3 to download file
                try:
                    s3 = boto3.client('s3', region_name=REGION, config=Config(signature_version='s3v4'))
                except ClientError as e:
                    return jsonify({"code": 500, "message": "Could not connect to s3"})
                
                download_file(s3, bucket, key, user, file_id)

                # launch annotation
                args = ['python', RUN_PY, f"{JOBS_DIR}{KEY_SEP}{user}{KEY_SEP}{file_id}{KEY_SEP}{file_id}", user_role]

                subprocess_ran = run_subprocess(args, job_id)
                if subprocess_ran:
                    # if subprocess runs successfully, update status to 'RUNNING'
                    table_updated = update_table(job_id)
                    if not table_updated:
                        # table couldn't be updated
                        app.logger.error("Message will be deleted because subprocess ran correctly, "\
                            "but table could not be updated to reflect 'RUNNING' status.")
                    # Delete the message from the queue, if job was successfully submitted
                    delete_message(message)
                else:
                    app.logger.error('Failed to run subprocess') 

    return jsonify({
        "code": 200, 
        "message": "Annotation job request processed."
        }), 200

#### HELPER FUNCTIONS ####

def create_dirs(user, file_id):
    """
    Creates directory locally to store file.
    """
    if not os.path.exists(f"{JOBS_DIR}{KEY_SEP}{user}"):
        os.makedirs(f"{JOBS_DIR}{KEY_SEP}{user}")
    try:
        os.makedirs(f"{JOBS_DIR}{KEY_SEP}{user}{KEY_SEP}{file_id}")
        app.logger.info("Created local directory")
    except:
        app.logger.info('Directory already exists')


def download_file(s3, bucket, key, user, file_id):
    """
    Helper function. Downloads file locally to run anntools.
    """
    try:
        s3.download_file(bucket, key, f"{JOBS_DIR}{KEY_SEP}{user}{KEY_SEP}{file_id}{KEY_SEP}{file_id}")
        app.logger.info('Downloaded file locally')
    except ClientError as error:
        e_message = error.response['Error']['Message']
        if e_message == 'Not Found':
            app.logger.error('Bucket does not exist')
        elif e_message == 'Forbidden':
            app.logger.error('Access to this bucket is forbidden')
        return jsonify({"code": 500, "error": "Could not download file to run annotation."})


def run_subprocess(args, job_id):
    """
    Helper function to try to initiate subprocess.
    Doing this to avoid nested try/excepts.
    Returns True if subprocess ran correctly, False otherwise. 
    """
    try:
        process = Popen(args)
        app.logger.info(f"running job_id {job_id}")
        return True
    except Exception as e:
        app.logger.error("subprocess didn't run")
        app.logger.error(e)
        return False


def update_table(job_id):
    """
    Helper function to try to conditionally update table.
    Doing this to avoid nested try/excepts.
    Returns True if table updates correctly, False otherwise. 
    """
    dynamodb = boto3.resource('dynamodb', region_name=REGION)
    ann_table = dynamodb.Table(DYNAMO)

    # if job status cannot be updated, a ClientError exception will be thrown
    try:
        update_response = ann_table.update_item(
            Key={
                'job_id': job_id,
            },
            UpdateExpression="SET job_status = :r",
            ConditionExpression="job_status = :p",
            ExpressionAttributeValues={
                ':r': RUNNING,
                ':p': PENDING
            }
            )
        app.logger.info('job status updated')
        return True
    except ClientError as ce:
        # error handling for table update
        if ce.response['Error']['Code'] == 'ConditionalCheckFailedException':
            app.logger.error('ConditionalCheckFailedException:', "Job could not be updated")
        else:
            app.logger.error('Could not update job status')
        return False


def delete_message(message):
    """
    Helper function to delete message.
    """
    try:
        message.delete()
        app.logger.info('message deleted')
    except ClientError as e:
        app.logger.error(e.response['Error']['Code'])
        app.logger.error('could not delete message from queue')


app.run('0.0.0.0', debug=True)

### EOF