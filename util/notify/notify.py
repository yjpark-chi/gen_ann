# notify.py
#
# Notify users of job completion
#
# Copyright (C) 2011-2021 Vas Vasiliadis
# University of Chicago
##
__author__ = 'Vas Vasiliadis <vas@uchicago.edu>'

import boto3
import time
import os
import sys
import json
import psycopg2
from botocore.exceptions import ClientError

# Import utility helpers
sys.path.insert(1, os.path.realpath(os.path.pardir))
import helpers

# Get configuration
from configparser import ConfigParser
config = ConfigParser(os.environ)
config.read('notify_config.ini')

REGION = config.get('aws', 'AwsRegionName')
QUEUE = config.get('sqs', 'AWS_SQS_RESULTS_QUEUE_NAME')
RESULTS_URL = config.get('gas', 'url')
AWS_SQS_WAIT_TIME = int(config.get('sqs', 'AWS_SQS_WAIT_TIME'))
AWS_SQS_MAX_MESSAGES = int(config.get('sqs', 'AWS_SQS_MAX_MESSAGES'))
EMAIL_SUBJECT = config.get('email', 'EMAIL_SUBJECT')
EMAIL_BODY = config.get('email', 'EMAIL_BODY')

'''Capstone - Exercise 3(d)
Reads result messages from SQS and sends notification emails.
'''
def handle_results_queue(sqs=None):
    # Read a message from the queue

    try:
        messages = sqs.receive_messages(WaitTimeSeconds=AWS_SQS_WAIT_TIME, MaxNumberOfMessages=AWS_SQS_MAX_MESSAGES)
    except ClientError as e:
        print("Could not receive messages from results queue.")
        print(e)

    for message in messages:
        message_info = json.loads(json.loads(message.body)['Message'])
        # Process message
        user_id = message_info.get('user_id', None)
        job_id = message_info.get('job_id', None)
        complete_time = message_info.get('complete_time', None)
        if complete_time:
            complete_time = time.strftime('%Y-%m-%d %H:%M', time.localtime(int(message_info['complete_time'])))
        
        if user_id and job_id:
            results_link = f"{RESULTS_URL}{job_id}"

            email_subject = EMAIL_SUBJECT.format(job_id=job_id)
            email_body = EMAIL_BODY.format(complete_time=complete_time, results_link=results_link)
        else:
            print("Could not retrieve job information from message queue. Email not sent.")
            continue
        
        try:
            profile = helpers.get_user_profile(user_id)
            recipient = [profile.get('email', None)]
        except psycopg2.Error as e:
            print("Could not retrieve user's email.")
            print(e)
        except ClientError as c:
            print(c)

        try:
            print('sending email')
            helpers.send_email_ses(recipients=recipient, subject=email_subject, body=email_body)
        except ClientError as e:
            print('Could not send email. Error retrieving user information.')
        except Exception as e:
            print('Could not send email')
            print(e)

        # Delete message
        try:
            message.delete()
        except ClientError as e:
            print('message could not be deleted')

if __name__ == '__main__':
  
    # Get handles to resources; and create resources if they don't exist
    
    try:
        sqs = boto3.resource('sqs', region_name=REGION)
        queue = sqs.get_queue_by_name(QueueName=QUEUE)
    except ClientError as e:
        print("Could not connect to message queue.")

    # Poll queue for new results and process them
    while True:
        handle_results_queue(sqs=queue)

### EOF
