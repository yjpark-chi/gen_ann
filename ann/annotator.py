import boto3
from subprocess import Popen
from botocore.client import Config
from botocore.exceptions import ClientError
import os
import json

# get config
from configparser import ConfigParser, ExtendedInterpolation
config = ConfigParser(os.environ, interpolation=ExtendedInterpolation())
config.read('ann_config.ini')

REGION = config.get('aws', 'AwsRegionName')
DYNAMO = config.get('dynamodb', 'AWS_DYNAMODB_ANNOTATIONS_TABLE')

# define helper functions to initiate subprocess and update table
def run_subprocess(args, job_id):
    """
    Helper function to try to initiate subprocess.
    Doing this to avoid nested try/excepts.
    Returns True if subprocess ran correctly, False otherwise. 
    """
    try:
        process = Popen(args)
        print(f"running job_id {job_id}")
        return True
    except Exception as e:
        print("subprocess didn't run")
        print(e)
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
            ':r': "RUNNING",
            ':p': "PENDING"
        }
        )
        print('job status updated')
        return True
    except ClientError as ce:
        # error handling for table update
        if ce.response['Error']['Code'] == 'ConditionalCheckFailedException':
            print('ConditionalCheckFailedException:', "job could not be updated")
        else:
            print('could not update job status')
        return False


def delete_message(message):
    """
    Helper function to delete message.
    """
    try:
        message.delete()
        print('message deleted')
    except ClientError as e:
        print(e.response['Error']['Code'])
        print('could not delete message from queue')


# Connect to SQS and get the message queue
sqs = boto3.resource('sqs', region_name=REGION)

# get queue
# https://boto3.amazonaws.com/v1/documentation/api/latest/guide/sqs.html#using-an-existing-queue

# getting messages and accessing their attributes
# https://boto3.amazonaws.com/v1/documentation/api/latest/guide/sqs.html#using-an-existing-queue


queue_name = config.get('sqs', 'AWS_SQS_REQUESTS_QUEUE_NAME')
queue = sqs.get_queue_by_name(QueueName=queue_name)

# Poll the message queue in a loop 
while True:
    # Attempt to read a message from the queue
    # Use long polling - DO NOT use sleep() to wait between polls

    # long polling
    # https://boto3.amazonaws.com/v1/documentation/api/1.9.42/guide/sqs-example-long-polling.html
    # https://stackoverflow.com/questions/50558084/how-to-long-poll-amazon-sqs-service-using-boto

    messages = queue.receive_messages(WaitTimeSeconds=20)
    for message in messages:
        job_info = json.loads(json.loads(message.body)['Message'])
        receipt_handle = message.receipt_handle
        
        job_id = job_info['job_id']
        user = job_info['user_id']
        input_file = job_info['input_file_name']
        bucket = config.get('s3', 'AWS_S3_INPUTS_BUCKET')
        key = job_info['s3_key_input_file']
        file_id = f"{job_id}~{input_file}"
    
        ##########################################
        # check if a user has a directory. if not, create one to store their job files
        # https://stackoverflow.com/questions/1274405/how-to-create-new-folder

        # download file from s3
        # https://boto3.amazonaws.com/v1/documentation/api/latest/guide/s3-example-download-file.html
        # https://stackoverflow.com/questions/29378763/how-to-save-s3-object-to-a-file-using-boto3
        if not os.path.exists(f"{os.getcwd()}/anntools/data/{user}"):
            os.makedirs(f"{os.getcwd()}/anntools/data/{user}")
        try:
            os.makedirs(f"{os.getcwd()}/anntools/data/{user}/{file_id}")
        except:
            print('directory already created')

        s3 = boto3.client('s3', region_name=REGION, config=Config(signature_version='s3v4'))

        try:
            s3.download_file(bucket, key, f"{os.getcwd()}/anntools/data/{user}/{file_id}/{file_id}")
        except ClientError as error:
            e_message = error.response['Error']['Message']
            if e_message == 'Not Found':
                print('Bucket does not exist')
            elif e_message == 'Forbidden':
                print('Access to this bucket is forbidden')

        ##########################################

        # Launch annotation job as a background process
        args = ['python', f"{os.getcwd()}/run.py", f"{os.getcwd()}/anntools/data/{user}/{file_id}/{file_id}"]

        subprocess_ran = run_subprocess(args, job_id)

        if subprocess_ran:
            # if subprocess runs successfully, update status to 'RUNNING'
            table_updated = update_table(job_id)
            if not table_updated:
                # table couldn't be updated
                print("Message will be deleted because subprocess ran correctly, "\
                    "but table could not be updated to reflect 'RUNNING' status.")
            # Delete the message from the queue, if job was successfully submitted
            delete_message(message)
        else:
            print("subprocess failed to spawn. please try again.")                
