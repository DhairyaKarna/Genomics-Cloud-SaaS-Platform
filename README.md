# Dhairya Karna A16 Data Restoration


## Rationale Behind Archival Workflow

The genomic annotation service employs a sophisticated workflow for handling the retrieval of the archived result files, particularly for premium users.

## Process Flow
1. **Subscription Upgrade**: Utilizes Stripe to create customers and subscriptions, upgrading users to premium status.
2. **Message Creation and Publishing**: Queries DynamoDB for jobs with `archive_id`, creating and publishing a message with `thaw_status: STARTED` to the `thaw_requests` SNS topic.
3. **Thaw Script**: Long polls SQS for messages, initiating Glacier job retrieval with `Expedited`, falling back to `Standard` if necessary. Updates DynamoDB with `RESTORING` status and publishes `IN PROGRESS` status messages.
4. **Thaw Completion Check**: Uses `glacier.describe_job` to check completion. If done, invokes a Lambda function with job details for restoration.
5. **Lambda Restoration**: Retrieves the job output from Glacier, uploads it to S3, updates DynamoDB status to `COMPLETED`, deletes `archive_id`, and removes the archive from Glacier.

## Technical Details
- **AWS Services Used:** S3, DynamoDB, Glacier, Lambda, SNS, SQS
- **Key Operations:**
  - **glacier.initiate_job** for archive retrieval
  - **glacier.describe_job** to check retrieval status
  - **glacier.get_job_output** and s3.put_object for restoring data to S3
  - **glacier.delete_archive** to remove the restored archive
  - DynamoDB updates for job status management

## Rationale

This process optimizes AWS resources by leveraging Glacier for cost-effective long-term storage and efficiently manages user data restoration with minimal manual intervention. I decided to use the get_job_output and put_object in Lambda because I did not want to send the streaming bytes through Lambda invocation as there is a limit on the size of the payload.
The integration with Stripe facilitates a smooth upgrade path for users, enhancing the service's usability and value.

## Rough Sketch
- The data/result file of the annotation job is archived for a free user after 3 minutes. Also, free users can't use files that are more than 150KB for their annotation job.
- For the user to get the result file again, they will have to go through the subscription form to become the premium user.  The subscription is the same as A15 where we use Stripe integration to send card information and purchase the premium plan.
- The flask route for subscribe in views.py will create a stripe customer and subscription and then upgrade the user to premium member. Then we query the dynamo db table and get all the job_ids for the user that have a archive_id .
- We create a json message for each of these jobs and publish it to the thaw_requests sns topic. We introduce a new attribute called thaw_status with the value STARTED to keep track of the restoration process.
- In the util instance, the thaw_script.py file is long polling the sqs associated with the sns and getting these messages that need to be restored. If the thaw status is STARTED for the message, then we initiate job process.
- First we try the initiate job with expedited retrieval type. If we encounter the InsufficientCapacityException then we try again with the Standard retrival type.
- Once the job has started, we create a new message with all the same details with the given message and then put thaw_status as IN PROGRESS and job status as RESTORING in the dynamo db table, and publish it to the sns. And we delete the old message.
- Now after the new message is polled and if the thaw status is IN PROGRESS, then we use the glacier.describe_job function and check the response['Completed'] to check if the thaw has completed or not.
- If not then we do nothing and the message will be polled again. If the thaw is completed  then it inovkes the dhairyakarna-restore lambda with the details of job, archive, thaw, s3 bucket, s3 result key file etc. and delete the message from the sqs.
- The lambda function uses the thaw id in the glacier. get job output to get the streaming body. we use the .read to convert it to streaming byte. We use the streaming byte object and the s3. put object function to put the object for the specified s3 object key.
- Then we change the status back to completed in the dynamo db table and delete the archive id. and then we also delete the archived object from the galacier.delete archive function. 
