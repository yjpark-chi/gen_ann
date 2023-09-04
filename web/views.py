# views.py
#
# Copyright (C) 2011-2022 Vas Vasiliadis
# University of Chicago
#
# Application logic for the GAS
#
##
__author__ = 'Vas Vasiliadis <vas@uchicago.edu>'

import uuid
import time
import json
from datetime import datetime
import sys

import boto3
from botocore.client import Config
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from flask import (abort, flash, redirect, render_template, 
  request, session, url_for, jsonify)

from app import app, db
from decorators import authenticated, is_premium

"""Start annotation request
Creates the required AWS S3 policy document and renders a form for
uploading an annotation input file using the policy document
"""
@app.route('/annotate/', methods=['GET'])
@authenticated
def annotate():
    # Open a connection to the S3 service
    s3 = boto3.client('s3', 
        region_name=app.config['AWS_REGION_NAME'], 
        config=Config(signature_version='s3v4'))

    bucket_name = app.config['AWS_S3_INPUTS_BUCKET']
    user_id = session['primary_identity']

    # Generate unique ID to be used as S3 key (name)
    key_name = app.config['AWS_S3_KEY_PREFIX'] + user_id + '/' + \
        str(uuid.uuid4()) + '~${filename}'

    # Create the redirect URL
    redirect_url = str(request.url) + "/job"

    # Define policy conditions
    encryption = app.config['AWS_S3_ENCRYPTION']
    acl = app.config['AWS_S3_ACL']
    fields = {
        "success_action_redirect": redirect_url,
        "x-amz-server-side-encryption": encryption,
        "acl": acl
    }
    conditions = [
        ["starts-with", "$success_action_redirect", redirect_url],
        {"x-amz-server-side-encryption": encryption},
        {"acl": acl}
    ]

    # Generate the presigned POST call
    try:
        presigned_post = s3.generate_presigned_post(
            Bucket=bucket_name, 
            Key=key_name,
            Fields=fields,
            Conditions=conditions,
            ExpiresIn=app.config['AWS_SIGNED_REQUEST_EXPIRATION'])
    except ClientError as e:
        app.logger.error(f'Unable to generate presigned URL for upload: {e}')
        return abort(500)

    # Render the upload form which will parse/submit the presigned POST
    return render_template('annotate.html',
        s3_post=presigned_post,
        role=session['role'])


"""Fires off an annotation job
Accepts the S3 redirect GET request, parses it to extract 
required info, saves a job item to the database, and then
publishes a notification for the annotator service.
"""
@app.route('/annotate/job', methods=['GET'])
@authenticated
def create_annotation_job_request():

    # logging vs. printing file=sys.stdout
    # https://stackoverflow.com/questions/44405708/flask-doesnt-print-to-console
    app.logger.info('creating the annotation request...')
    region = app.config['AWS_REGION_NAME']

    # Parse redirect URL query parameters for S3 object info
    bucket_name = request.args.get('bucket')
    s3_key = request.args.get('key')

    # Extract the job ID from the S3 key
    # Move your code here

    file_info, input_file = s3_key.split('~')
    _, _, job_id = file_info.split('/')
    user_id = session['primary_identity']

    entry = {"job_id": job_id, 
        "user_id": user_id, 
        "input_file_name": input_file,
        "s3_inputs_bucket": bucket_name,
        "s3_key_input_file": s3_key,
        "submit_time": int(time.time()),
        "job_status": app.config["PENDING"],
        }

    # Persist job to database
    try:
        dynamodb = boto3.resource('dynamodb', region_name=region)
        ann_table = dynamodb.Table(app.config['AWS_DYNAMODB_ANNOTATIONS_TABLE'])
    except ClientError as e:
        app.logger.error(e)

    try:
        ann_table.put_item(Item=entry)
    except ClientError as e:
        app.logger.error('could not add job to dynamodb.')
        app.logger.error(e.response['Error']['Code'])

    # Send message to request queue

    # Add user role to entry
    entry["user_role"] =  session['role']
    arn = app.config['AWS_SNS_JOB_REQUEST_TOPIC']

    try:
        sns = boto3.client('sns', region_name=region)
        response = sns.publish(
            TopicArn=arn,
            Message=json.dumps(entry),
        )
    except ClientError as e:
        app.logger.error(e.response['Error']['Code'])
        return abort(500)

    return render_template('annotate_confirm.html', job_id=job_id)


"""List all annotations for the user
"""
@app.route('/annotations', methods=['GET'])
@authenticated
def annotations_list():

    # Get list of annotations to display
    # how to query on secondary index
    # https://stackoverflow.com/questions/35758924/how-do-we-query-on-a-secondary-index-of-dynamodb-using-boto3

    region = app.config['AWS_REGION_NAME']
    user_id = session['primary_identity']
    print(session['role'])
    try:
        dynamodb = boto3.resource('dynamodb', region_name=region)
        ann_table = dynamodb.Table(app.config['AWS_DYNAMODB_ANNOTATIONS_TABLE'])
    except ClientError as e:
        app.logger.error(e)
        return jsonify({"code": 500, "message": "Could not connect to dynamodb"}), 500

    try: # query table
        response = ann_table.query(
            IndexName='user_id_index',
            KeyConditionExpression=Key('user_id').eq(user_id)
            )
    except ClientError as e:
        app.logger.error(e)
        return jsonify({"code": 500, "message": "Could not connect to dynamodb"}), 500

    # converting date
    # https://stackoverflow.com/questions/12400256/converting-epoch-time-into-the-datetime
    response = response['Items']
    for r in response:
        r['submit_time'] = time.strftime('%Y-%m-%d %H:%M', time.localtime(r['submit_time']))
    return render_template('annotations.html', annotations=response)


"""Display details of a specific annotation job
"""
@app.route('/annotations/<id>', methods=['GET'])
@authenticated
def annotation_details(id):
    job_id = id
    current_user = session['primary_identity']

    # connect to table and query

    region = app.config['AWS_REGION_NAME']
    try:
        dynamodb = boto3.resource('dynamodb', region_name=region)
        db_name = app.config['AWS_DYNAMODB_ANNOTATIONS_TABLE']
        ann_table = dynamodb.Table(db_name)
    except ClientError as e:
        app.logger.error("Could not connect to dynamodb")
        return abort(500)

    # make query
    # https://www.fernandomc.com/posts/ten-examples-of-getting-data-from-dynamodb-with-python-and-boto3/
    
    try:
        response = ann_table.get_item(
            Key={
                'job_id': job_id,
                }
            )
        response = response['Item']
    except ClientError as e:
        app.logger.error(e)
        return abort(500)

    # check if current user == user who requested job
    if response['user_id'] != current_user:
        app.logger.error("User does not have access to this job")
        return abort(403)

    # get status and convert time
    status = response['job_status']
    request_time = time.strftime('%Y-%m-%d %H:%M', time.localtime(response['submit_time']))
    input_file = response['input_file_name']
    complete_time = None
    
    # get completion time
    if status == 'COMPLETED':
        complete_time = response['complete_time']
        complete_time = time.strftime('%Y-%m-%d %H:%M', time.localtime(complete_time))

        results_file = response['s3_key_result_file']


    try:
        s3 = boto3.client('s3', 
            region_name=region, 
            config=Config(signature_version='s3v4'))
    except ClientError as e:
        app.logger.error("Could not connect to S3.")


    # generate download url for input file
    try:
        input_url = s3.generate_presigned_url('get_object',
                Params={'Bucket': app.config['AWS_S3_INPUTS_BUCKET'],
                'Key': response['s3_key_input_file']},
                ExpiresIn=app.config['AWS_SIGNED_REQUEST_EXPIRATION'])
    except ClientError as e:
        app.logger.error(e)
        input_url = None

    archived = response.get('results_file_archive_id', None)
    role = session['role']
    show_upgrade = None
    results_url = None
    print(role, file=sys.stderr)

    if complete_time:
        # get download links for results and log files
        # https://boto3.amazonaws.com/v1/documentation/api/latest/guide/s3-presigned-urls.html
        # https://stackoverflow.com/questions/60163289/how-do-i-create-a-presigned-url-to-download-a-file-from-an-s3-bucket-using-boto3

        # results file
        results_file = response['s3_key_result_file']
        _, _, results_file = results_file.split('/')
        results_file = response['s3_key_input_file'] + app.config['KEY_SEP'] + results_file

        if not archived:
            results_url = generate_results_url(s3, results_file)
        
        elif archived and role == 'premium_user':
            # check if object was previously restored to S3
            # connect to s3 resource and then to object resource
            try:
                s3_resource = boto3.resource('s3', region_name=region)
            except ClientError as c:
                print(e)
                app.logger.error("Could not connect to s3 resource.")

            # checking if S3 object exists
            # https://stackoverflow.com/questions/33842944/check-if-a-key-exists-in-a-bucket-in-s3-using-boto3
            obj = None
            try:
                print('this is results file', results_file)
                obj =  s3_resource.Object(app.config['AWS_S3_RESULTS_BUCKET'], results_file).get()
            except ClientError as e:
                if e.response['Error']['Code'] == 'AccessDenied':
                    app.logger.error("Access Denied. Check bucket settings.")
                elif e.response['Error']['Code'] == 'NoSuchKey':
                    app.logger.error("Object not in S3. Restoration in process.")
            if obj:
                results_url = generate_results_url(s3, results_file)
            else: # object is not in S3 and is in the process of being retrieved.
                show_upgrade = 'in progress'

        elif archived and role == 'free_user':
            results_url = app.config['PREMIUM_ENDPOINT']
            show_upgrade = 'upgrade'

        job_data = {
            'request_id': job_id,
            'request_time': request_time,
            'input_file': input_file,
            'status': status,
            'complete_time': complete_time,
            'input_url': input_url,
            'results_url': results_url
            }
    
    else:
        job_data = {
            'request_id': job_id,
            'request_time': request_time,
            'input_file': input_file,
            'status': status,
            'input_url': input_url
            }

    return render_template('annotation.html', annotation=job_data, show_upgrade=show_upgrade)

def generate_results_url(s3, results_file):
    try:
        results_url = s3.generate_presigned_url('get_object',
            Params={'Bucket': app.config['AWS_S3_RESULTS_BUCKET'],
                    'Key': results_file},
                    ExpiresIn=app.config['AWS_SIGNED_REQUEST_EXPIRATION'])
        return results_url
    except ClientError as e:
        app.logger.error(e)
        return None


"""Display the log file contents for an annotation job
"""
@app.route('/annotations/<id>/log', methods=['GET'])
@authenticated
def annotation_log(id):

    job_id = id

    # connect to table and query
    region = app.config['AWS_REGION_NAME']
    try:
        dynamodb = boto3.resource('dynamodb', region_name=region)
        db_name = app.config['AWS_DYNAMODB_ANNOTATIONS_TABLE']
        ann_table = dynamodb.Table(db_name)
    except ClientError as e:
        app.logger.error("Could not connect to dynamodb")
        return abort(500)
    
    # make query
    # https://www.fernandomc.com/posts/ten-examples-of-getting-data-from-dynamodb-with-python-and-boto3/
    try:
        response = ann_table.get_item(
            Key={
                'job_id': job_id,
                }
            )
        response = response['Item']
    except ClientError as e:
        app.logger.error(e)
        return abort(500)

    # check that user_id in job equals current user
    current_user = session['primary_identity']
    if current_user != response['user_id']:
        app.logger.error("User does not have access to this job")
        return abort(403)

    # log file
    log_file = response['s3_key_log_file']
    _, _, log_file = log_file.split('/')
    log_file = response['s3_key_input_file'] + '/' + log_file

    # get s3 object
    # https://stackoverflow.com/questions/31976273/open-s3-object-as-a-string-with-boto3
    s3 = boto3.client('s3', 
        region_name=region, 
        config=Config(signature_version='s3v4'))

    try:
        log_object = s3.get_object(
            Bucket=app.config['AWS_S3_RESULTS_BUCKET'],
            Key=log_file)
    except ClientError as e:
        app.logger.error(e)
        log_url = None

    # read log object
    # https://stackoverflow.com/questions/31976273/open-s3-object-as-a-string-with-boto3
    log_url = log_object['Body'].read().decode('utf-8')
    
    # format so jinja for loop outputs correctly
    # https://stackoverflow.com/questions/33232830/newline-and-dash-not-working-correctly-in-jinja
    log_url = log_url.split('\n')
    return render_template('view_log.html', job_id=job_id, log_file=log_url)


"""Subscription management handler
"""
import stripe
from auth import update_profile

@app.route('/subscribe', methods=['GET', 'POST'])
@authenticated
def subscribe():

    if (request.method == 'GET'):
        # Display form to get subscriber credit card info
        return render_template('subscribe.html')

    elif (request.method == 'POST'):
        # Process the subscription request
        stripe_token = request.form['stripe_token']

    # Create a customer on Stripe
    user_id = session['primary_identity']
    stripe.api_key = app.config['STRIPE_SECRET_KEY']
    price_id = app.config['STRIPE_PRICE_ID']

    # stripe error handling
    # https://stripe.com/docs/error-handling
    # https://stackoverflow.com/questions/53148112/python-3-handling-error-typeerror-catching-classes-that-do-not-inherit-from-bas
    try: # create stripe customer
        customer = stripe.Customer.create(
            card=stripe_token,
            email=session['email'],
            name=session['name'],
            )
    except stripe.error.CardError as e:
        app.logger.error(e)
        return abort(400)
    except stripe.error.InvalidRequestError:
        app.logger.error("An invalid request occurred.")
        return abort(400)
    except Exception:
        app.logger.error("Another problem occurred, maybe unrelated to Stripe.")
        return abort(500)


    # Subscribe customer to pricing plan
    # https://stripe.com/docs/api?lang=python
    stripe.Subscription.create(
      customer=customer['id'],
      items=[
        {"price": price_id},
        ],
        )

    # Update user role in accounts database

    try:
        update_profile(
            identity_id=session['primary_identity'],
            role="premium_user"
            )
    except Exception as e:
        # generic exception because update_profile does not specify an exception
        app.logger.exception(e)

    session['role'] = "premium_user"
    # clear messages waiting to be archived
    # Cancel any pending archivals, i.e., jobs that recently completed and are awaiting archival
    # after the 5 minute grace period.    
    
    try:
        sqs = boto3.resource('sqs', region_name=app.config['AWS_REGION_NAME'])
        queue = sqs.Queue(app.config['ARCHIVE_QUEUE_URL'])
    except ClientError as e:
        app.logger.error(e)
        # if we can't connect to queue, just return template for now

    try:
        messages = queue.receive_messages(WaitTimeSeconds=app.config['AWS_SQS_WAIT_TIME'])
        for message in messages:
            message_info = json.loads(json.loads(message.body)['Message'])
            message_user_id = message_info.get('user_id', None)
            if message_user_id == user_id:
                message.delete()

    except ClientError as e:
        print("Could not purge archive queue")
        app.logger.error(e)


    # Request restoration of the user's data from Glacier

    # publish archive initiation request to archive sns
    thaw_arn = app.config['AWS_THAW_SNS']
    thaw_entry = {
            "user_id": user_id,
            "user_role": session['role']
            }
    try:
        sns = boto3.client('sns', region_name=app.config['AWS_REGION_NAME'])
        response = sns.publish(
            TopicArn=thaw_arn,
            Message=json.dumps(thaw_entry),
        )
        print('thaw sns sent')
    except ClientError as e:
        app.logger.error(e.response['Error']['Code'])
        return abort(500)

    # Display confirmation page

    return render_template('subscribe_confirm.html',stripe_id=customer['id'])


"""Set premium_user role
"""
@app.route('/make-me-premium', methods=['GET'])
@authenticated
def make_me_premium():
    # Hacky way to set the user's role to a premium user; simplifies testing
    update_profile(
        identity_id=session['primary_identity'],
        role="premium_user"
        )
    return redirect(url_for('profile'))


"""Reset subscription
"""
@app.route('/unsubscribe', methods=['GET'])
@authenticated
def unsubscribe():
  # Hacky way to reset the user's role to a free user; simplifies testing
    update_profile(
        identity_id=session['primary_identity'],
        role="free_user"
        )
    return redirect(url_for('profile'))


"""DO NOT CHANGE CODE BELOW THIS LINE
*******************************************************************************
"""

"""Home page
"""
@app.route('/', methods=['GET'])
def home():
  return render_template('home.html')

"""Login page; send user to Globus Auth
"""
@app.route('/login', methods=['GET'])
def login():
  app.logger.info(f"Login attempted from IP {request.remote_addr}")
  # If user requested a specific page, save it session for redirect after auth
  if (request.args.get('next')):
    session['next'] = request.args.get('next')
  return redirect(url_for('authcallback'))

"""404 error handler
"""
@app.errorhandler(404)
def page_not_found(e):
  return render_template('error.html', 
    title='Page not found', alert_level='warning',
    message="The page you tried to reach does not exist. \
      Please check the URL and try again."
    ), 404

"""403 error handler
"""
@app.errorhandler(403)
def forbidden(e):
  return render_template('error.html',
    title='Not authorized', alert_level='danger',
    message="You are not authorized to access this page. \
      If you think you deserve to be granted access, please contact the \
      supreme leader of the mutating genome revolutionary party."
    ), 403

"""405 error handler
"""
@app.errorhandler(405)
def not_allowed(e):
  return render_template('error.html',
    title='Not allowed', alert_level='warning',
    message="You attempted an operation that's not allowed; \
      get your act together, hacker!"
    ), 405

"""500 error handler
"""
@app.errorhandler(500)
def internal_error(error):
  return render_template('error.html',
    title='Server error', alert_level='danger',
    message="The server encountered an error and could \
      not process your request."
    ), 500

### EOF