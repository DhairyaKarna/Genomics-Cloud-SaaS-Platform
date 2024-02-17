# views.py
#
# Copyright (C) 2015-2023 Vas Vasiliadis
# University of Chicago
#
# Application logic for the GAS
#
##
__author__ = "Vas Vasiliadis <vas@uchicago.edu>"

import uuid
import time
import json
from datetime import datetime, timedelta, timezone

import boto3
from botocore.client import Config
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from flask import abort, flash, redirect, render_template, request, session, url_for

from app import app, db
from decorators import authenticated, is_premium

"""Start annotation request
Create the required AWS S3 policy document and render a form for
uploading an annotation input file using the policy document

Note: You are welcome to use this code instead of your own
but you can replace the code below with your own if you prefer.
"""


@app.route("/annotate", methods=["GET"])
@authenticated
def annotate():
    # Open a connection to the S3 service
    s3 = boto3.client(
        "s3",
        region_name=app.config["AWS_REGION_NAME"],
        config=Config(signature_version="s3v4"),
    )

    bucket_name = app.config["AWS_S3_INPUTS_BUCKET"]
    user_id = session["primary_identity"]

    # Generate unique ID to be used as S3 key (name)
    key_name = (
        app.config["AWS_S3_KEY_PREFIX"]
        + user_id
        + "/"
        + str(uuid.uuid4())
        + "~${filename}"
    )

    # Create the redirect URL
    redirect_url = str(request.url) + "/job"

    # Define policy conditions
    encryption = app.config["AWS_S3_ENCRYPTION"]
    acl = app.config["AWS_S3_ACL"]
    fields = {
        "success_action_redirect": redirect_url,
        "x-amz-server-side-encryption": encryption,
        "acl": acl,
        "csrf_token": app.config["SECRET_KEY"],
    }
    conditions = [
        ["starts-with", "$success_action_redirect", redirect_url],
        {"x-amz-server-side-encryption": encryption},
        {"acl": acl},
        ["starts-with", "$csrf_token", ""],
    ]

    # Generate the presigned POST call
    try:
        presigned_post = s3.generate_presigned_post(
            Bucket=bucket_name,
            Key=key_name,
            Fields=fields,
            Conditions=conditions,
            ExpiresIn=app.config["AWS_SIGNED_REQUEST_EXPIRATION"],
        )
    except ClientError as e:
        app.logger.error(f"Unable to generate presigned URL for upload: {e}")
        return abort(500)

    # Render the upload form which will parse/submit the presigned POST
    return render_template(
        "annotate.html", s3_post=presigned_post, role=session["role"]
    )


"""Fires off an annotation job
Accepts the S3 redirect GET request, parses it to extract 
required info, saves a job item to the database, and then
publishes a notification for the annotator service.

Note: Update/replace the code below with your own from previous
homework assignments
"""


@app.route("/annotate/job", methods=["GET"])
def create_annotation_job_request():

    region = app.config["AWS_REGION_NAME"]

    # Parse redirect URL query parameters for S3 object info
    bucket_name = request.args.get("bucket")
    s3_key = request.args.get("key")

    if not bucket_name or not s3_key:
        return jsonify({"code": 400, "status": "error", "message": "Missing bucket or key parameters"}), 400

    # Extract the job ID from the S3 key
    file_name = s3_key.split('/')[-1]
    job_id, file_name = file_name.split('~')

    # Current time as epoch for submit_time
    # https://www.programiz.com/python-programming/time
    submit_time = int(time.time())

    # Get the user ID from the session
    user_id = session["primary_identity"]

    # Preparing data for DynamoDB
    data = {
        'job_id': job_id,
        'user_id': user_id,
        "input_file_name": file_name, 
        "s3_inputs_bucket": bucket_name,
        "s3_key_input_file": s3_key,
        "submit_time": submit_time,
        'job_status': 'PENDING'
    }


    # Persist job to database
    dynamodb = boto3.resource('dynamodb', region_name=app.config["AWS_REGION_NAME"])
    table = dynamodb.Table(app.config["AWS_DYNAMODB_ANNOTATIONS_TABLE"])

    try:
        # Persist job details to DynamoDB
        # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/dynamodb/client/put_item.html
        table.put_item(Item=data)
    except ClientError as e:
        error_code = e.response['Error']['Code']
        return jsonify({"code": 500, "status": "error", "message": f"DynamoDB client error: {error_code}"}), 500
    except Exception as e:
        return jsonify({"code": 500, "status": "error", "message": f"Error persisting job details to DynamoDB: {e}"}), 500

    # Send message to request queue
    sns = boto3.client('sns', region_name=app.config["AWS_REGION_NAME"])

    try:
        # Construct and send the POST request to the annotator
        # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/sns/client/publish.html
        sns.publish(TopicArn=app.config['AWS_SNS_JOB_REQUEST_TOPIC'], Message = json.dumps(data), Subject = 'Job Submission')

    except ClientError as e:
        error_code = e.response['Error']['Code']
        return jsonify({"code": 500, "status": "error", "message": f"DynamoDB client error: {error_code}"}), 500
    except BotoCoreError as e:
        return jsonify({"code": 500, "message": f"Boto core error in generating signed request: {e}"}), 500

    return render_template("annotate_confirm.html", job_id=job_id)


""" Convert epoch time to CST
"""

def epoch_to_CST(epoch_time):
    # Convert epoch to datetime in UTC
    utc_time = datetime.fromtimestamp(int(epoch_time), timezone.utc)

    # Defining the CST offset: UTC-6 hours
    cst_offset = timedelta(hours=-6)

    # Applying the CST offset to UTC time
    cst_time = utc_time + cst_offset

    # Formatting the time as needed
    return cst_time.strftime('%Y-%m-%d %H:%M:%S')

"""List all annotations for the user
"""


@app.route("/annotations", methods=["GET"])
@authenticated
def annotations_list():

    user_id = session["primary_identity"]

    # Query the DynamoDB table using the secondary index
    dynamodb = boto3.resource('dynamodb', region_name=app.config["AWS_REGION_NAME"])
    table = dynamodb.Table(app.config["AWS_DYNAMODB_ANNOTATIONS_TABLE"])

    try:
        response = table.query(
            IndexName='user_id_index',  
            KeyConditionExpression=Key('user_id').eq(user_id)
        )
    except ClientError as e:
        app.logger.error(f"Error querying DynamoDB: {e}")
        abort(500)  # Internal Server Error

    except Exception as e:
        # Handle all other possible exceptions
        app.logger.error(f"Unexpected error: {str(e)}")
        abort(500)  # Internal Server Error


    # Get list of annotations to display
    jobs = response.get('Items', [])


    # Check if there are no jobs
    if not jobs:
        message = "No annotations found."
        return render_template("annotations.html", message=message)

    # Convert epoch to human-readable date-time in CST
    for job in jobs:
        
        # Converting time to CST
        job['submit_time'] = epoch_to_CST(job['submit_time'])


    return render_template("annotations.html", annotations=jobs)


"""Display details of a specific annotation job
"""


@app.route("/annotations/<job_id>", methods=["GET"])
def annotation_details(job_id):
    user_id = session.get("primary_identity")

    # Query the DynamoDB table for the job details
    dynamodb = boto3.resource('dynamodb', region_name=app.config["AWS_REGION_NAME"])
    table = dynamodb.Table(app.config["AWS_DYNAMODB_ANNOTATIONS_TABLE"])

    try:
        response = table.get_item(Key={'job_id': job_id})
        job = response.get('Item', None)
        
        # Check if the job exists and belongs to the user
        if not job or job.get('user_id') != user_id:
            abort(403)  # Forbidden if job doesn't exist or doesn't belong to the user

        # Convert epoch times to CST
        job['request_time_formatted'] = epoch_to_CST(job['submit_time'])
        if 'complete_time' in job and job['job_status'] == 'COMPLETED':
            job['complete_time_formatted'] = epoch_to_CST(job['complete_time'])

        return render_template("annotation.html", job=job)
        
    except ClientError as e:
        app.logger.error(f"Error fetching job details from DynamoDB: {e}")
        abort(500)  # Internal Server Error


    pass


"""Display the log file contents for an annotation job
"""


@app.route("/annotations/<id>/log", methods=["GET"])
def annotation_log(id):
    pass


"""Subscription management handler
"""
import stripe
from auth import update_profile


@app.route("/subscribe", methods=["GET", "POST"])
def subscribe():
    if request.method == "GET":
        # Display form to get subscriber credit card info

        # If A15 not completed, force-upgrade user role and initiate restoration
        pass

    elif request.method == "POST":
        # Process the subscription request

        # Create a customer on Stripe

        # Subscribe customer to pricing plan

        # Update user role in accounts database

        # Update role in the session

        # Request restoration of the user's data from Glacier
        # ...add code here to initiate restoration of archived user data
        # ...and make sure you handle files pending archive!

        # Display confirmation page
        pass


"""DO NOT CHANGE CODE BELOW THIS LINE
*******************************************************************************
"""

"""Set premium_user role
"""


@app.route("/make-me-premium", methods=["GET"])
@authenticated
def make_me_premium():
    # Hacky way to set the user's role to a premium user; simplifies testing
    update_profile(identity_id=session["primary_identity"], role="premium_user")
    return redirect(url_for("profile"))


"""Reset subscription
"""


@app.route("/unsubscribe", methods=["GET"])
@authenticated
def unsubscribe():
    # Hacky way to reset the user's role to a free user; simplifies testing
    update_profile(identity_id=session["primary_identity"], role="free_user")
    return redirect(url_for("profile"))


"""Home page
"""


@app.route("/", methods=["GET"])
def home():
    return render_template("home.html"), 200


"""Login page; send user to Globus Auth
"""


@app.route("/login", methods=["GET"])
def login():
    app.logger.info(f"Login attempted from IP {request.remote_addr}")
    # If user requested a specific page, save it session for redirect after auth
    if request.args.get("next"):
        session["next"] = request.args.get("next")
    return redirect(url_for("authcallback"))


"""404 error handler
"""


@app.errorhandler(404)
def page_not_found(e):
    return (
        render_template(
            "error.html",
            title="Page not found",
            alert_level="warning",
            message="The page you tried to reach does not exist. \
      Please check the URL and try again.",
        ),
        404,
    )


"""403 error handler
"""


@app.errorhandler(403)
def forbidden(e):
    return (
        render_template(
            "error.html",
            title="Not authorized",
            alert_level="danger",
            message="You are not authorized to access this page. \
      If you think you deserve to be granted access, please contact the \
      supreme leader of the mutating genome revolutionary party.",
        ),
        403,
    )


"""405 error handler
"""


@app.errorhandler(405)
def not_allowed(e):
    return (
        render_template(
            "error.html",
            title="Not allowed",
            alert_level="warning",
            message="You attempted an operation that's not allowed; \
      get your act together, hacker!",
        ),
        405,
    )


"""500 error handler
"""


@app.errorhandler(500)
def internal_error(error):
    return (
        render_template(
            "error.html",
            title="Server error",
            alert_level="danger",
            message="The server encountered an error and could \
      not process your request.",
        ),
        500,
    )


"""CSRF error handler
"""


from flask_wtf.csrf import CSRFError


@app.errorhandler(CSRFError)
def csrf_error(error):
    return (
        render_template(
            "error.html",
            title="CSRF error",
            alert_level="danger",
            message=f"Cross-Site Request Forgery error detected: {error.description}",
        ),
        400,
    )


### EOF
