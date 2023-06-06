# run.py
#
# Copyright (C) 2011-2019 Vas Vasiliadis
# University of Chicago
#
# Wrapper script for running AnnTools
#
##
__author__ = 'Vas Vasiliadis <vas@uchicago.edu>'

import sys
import time
import driver
# new imports
import shutil
import os
import boto3 
from botocore.client import Config
from botocore.exceptions import ClientError
import json

# get config
from configparser import ConfigParser, ExtendedInterpolation
config = ConfigParser(os.environ, interpolation=ExtendedInterpolation())
config.read('ann_config.ini')

CNET = config.get('ann', 'CNET')
REGION = config.get('aws', 'AwsRegionName')
BUCKET = config.get('s3', 'AWS_S3_RESULTS_BUCKET')
DYNAMO = config.get('dynamodb', 'AWS_DYNAMODB_ANNOTATIONS_TABLE')
SNS_ARN = config.get('sns', 'AWS_SNS_JOB_RESULTS_TOPIC')
JOBS_DIR = config.get('ann', 'ANNOTATOR_JOBS_DIR')
ANNOT = config.get('ann', 'ANNOT')
LOG = config.get('ann', 'LOG')
COMPLETED = config.get('ann', 'COMPLETED')
SM_ARN = config.get('sm', 'AWS_STATE_MACHINE_ARN')
RETURN_OPT = config.get('dynamodb', 'RETURN_OPT')
KEY_SEP = config.get('ann', 'KEY_SEP')
FILE_SEP = config.get('ann', 'FILE_SEP')
VCF = config.get('ann', 'VCF')


class Results:
    def __init__(self):

        file_path = sys.argv[1]
        split_file = sys.argv[1].split(KEY_SEP)
        self.user = split_file[-3]
        self.job_id_file = split_file[-1]
        self.job_id, self.input_file = self.job_id_file.split(FILE_SEP)

        self.jobs_direc = f"{JOBS_DIR}{KEY_SEP}{self.user}{KEY_SEP}{self.job_id_file}"

        self.s3 = boto3.client('s3', region_name=REGION, config=Config(signature_version='s3v4'))
    
        self.user_role = sys.argv[2]

    def upload_annot_file(self):
        # uploading a file
        # https://boto3.amazonaws.com/v1/documentation/api/latest/guide/s3-uploading-files.html
        # https://stackoverflow.com/questions/15085864/how-to-upload-a-file-to-directory-in-s3-bucket-using-boto
        # https://www.learnaws.org/2022/07/13/boto3-upload-files-s3/
        
        self.annot_file = self.job_id + FILE_SEP + self.input_file.replace(VCF, '') + ANNOT

        try:
            print('uploading .annot file')
            response_annot = self.s3.upload_file(f"{self.jobs_direc}{KEY_SEP}{self.annot_file}", BUCKET,
                f"{CNET}{KEY_SEP}{self.user}{KEY_SEP}{self.job_id_file}{KEY_SEP}{self.annot_file}")
        except FileNotFoundError:
            print("Subprocess did not generate annotator file. Please try again.")
        except ClientError as error:
            if error.response['Error']['Code'] == 'AccessDenied':
                print("Access Denied.")
            elif error.response['Error']['Code'] == 'NoSuchBucket':
                print('Bucket does not exist')
    
    def upload_log_file(self):
        self.log_file = self.job_id + FILE_SEP + self.input_file + LOG

        # attempt to upload log file
        try:
            # attempt to upload log file
            print('uploading .log file')
            response_log = self.s3.upload_file(f"{self.jobs_direc}{KEY_SEP}{self.log_file}", BUCKET,
                f"{CNET}{KEY_SEP}{self.user}{KEY_SEP}{self.job_id_file}{KEY_SEP}{self.log_file}")
        except FileNotFoundError:
            # ignore annotation failures
            print("Subprocess did not generate log file. Please try again.")
        except ClientError as error:
            if error.response['Error']['Code'] == 'AccessDenied':
                print("Access Denied.")
            elif error.response['Error']['Code'] == 'NoSuchBucket':
                print('Bucket does not exist')


"""A rudimentary timer for coarse-grained profiling
"""
class Timer(object):
  def __init__(self, verbose=True):
    self.verbose = verbose

  def __enter__(self):
    self.start = time.time()
    return self

  def __exit__(self, *args):
    self.end = time.time()
    self.secs = self.end - self.start
    if self.verbose:
      print(f"Approximate runtime: {self.secs:.2f} seconds")

if __name__ == '__main__':
    # Call the AnnTools pipeline
    if len(sys.argv) <= 1:
        print("A valid .vcf file must be provided as input to this program.")
    else:
        with Timer():
            driver.run(sys.argv[1], 'vcf')

        # initialize results files
        results = Results()

        # upload the results files in a directory for their job
        results.upload_annot_file()
        results.upload_log_file()

        # update table and get return values
        # https://stackoverflow.com/questions/34447304/example-of-update-item-in-dynamodb-boto3
        try:
            complete_time = int(time.time())
            dynamodb = boto3.resource('dynamodb', region_name=REGION)
            ann_table = dynamodb.Table(DYNAMO)
            table_response = ann_table.update_item(
                    Key={
                        'job_id': results.job_id,
                    },
                    UpdateExpression="set s3_results_bucket = :r, s3_key_result_file = :a, s3_key_log_file =:l,\
                    complete_time=:t, job_status=:v",
                    ExpressionAttributeValues={
                        ':r': BUCKET,
                        ':a': f"{CNET}{KEY_SEP}{results.user}{KEY_SEP}{results.annot_file}",
                        ':l': f"{CNET}{KEY_SEP}{results.user}{KEY_SEP}{results.log_file}", 
                        ':t': complete_time,
                        ':v': COMPLETED
                    },
                    ReturnValues=RETURN_OPT,
                )
            print('Updated table status')
            user_role = table_response['Attributes'].get('user_role', None)
        except ClientError as e:
            print("could not update table with 'COMPLETED' status")
            print(e.response['Error']['Code'])

        #### publish a notification that the job is complete ####

        sns = boto3.client('sns', region_name=REGION)
        sns_message = {
                    'user_id': results.user,
                    'job_id': results.job_id,
                    'complete_time': str(complete_time),
                    'input_file': results.input_file
                    }
        try:
            response = sns.publish(
                TopicArn=SNS_ARN,
                Message=json.dumps(sns_message)
            )
        except ClientError as e:
            print('could not publish message to topic')
            print(e.response['Error']['Code'])


        #### execute step function ####
        if results.user_role == 'free_user':
            sf = boto3.client('stepfunctions', region_name=REGION)
            
            try:
                sf_response = sf.start_execution(
                    stateMachineArn=SM_ARN,
                    input=json.dumps(sns_message)
                    )
                print('Free user: step function executed.')
            except:
                print('ERROR: Could not activate step function.')

        #### local delete files ####
        # https://stackoverflow.com/questions/6996603/how-do-i-delete-a-file-or-folder-in-python

        # check if user's directory has no other jobs before deleting the directory
        # this is to prevent deleting a user's ongoing jobs
        existing_dirs = os.listdir(f"{JOBS_DIR}{KEY_SEP}{results.user}")
        if len(existing_dirs) == 1 and existing_dirs[0] == results.job_id_file:
            shutil.rmtree(f"{JOBS_DIR}{KEY_SEP}{results.user}")
            print(f"user's directory and files for {results.job_id} were deleted.")
        else:
            shutil.rmtree(f"{JOBS_DIR}{KEY_SEP}{results.user}{KEY_SEP}{results.job_id_file}")
            print(f"files associated with job_id {results.job_id} were deleted.")


### EOF