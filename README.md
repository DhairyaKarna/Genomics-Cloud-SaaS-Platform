# Genomics Cloud SaaS Platform

Developed a sophisticated cloud-based workflow for processing genomic data and annotate them, utilizing AWS services such as S3, DynamoDB, Glacier, Lambda, SNS, and SQS, along with Stripe integration for user subscription management.

## Features

- **AWS Integration:** Utilizes S3, DynamoDB, Glacier, Lambda, SNS, and SQS for efficient data processing and storage.
- **Stripe Integration:** Handles user subscriptions and payments.
- **Automated Workflow:** Automates data retrieval and processing, reducing manual intervention.

## Workflow Overview

1. **Subscription Upgrade:** Utilizes Stripe to upgrade users to premium.
2. **Message Handling:** Publishes messages to SNS, manages SQS for long polling.
3. **Data Retrieval:** Uses Glacier for data archiving and retrieval.
4. **Lambda Processing:** Manages data restoration and updates DynamoDB.

## Technical Details

- **AWS Services:** S3, DynamoDB, Glacier, Lambda, SNS, SQS.
- **Key Operations:**
  - `glacier.initiate_job` for archive retrieval.
  - `glacier.describe_job` for job status.
  - `glacier.get_job_output` and `s3.put_object` for data restoration.
  - `glacier.delete_archive` for archive management.
  - DynamoDB for job status updates.
 

### Key Operations

- **Initiate Archive Retrieval:**
  ```python
  glacier.initiate_job(
      vaultName='your-vault-name',
      jobParameters={
          'Type': 'archive-retrieval',
          'ArchiveId': 'your-archive-id',
          'SNSTopic': 'arn:aws:sns:your-region:your-account-id:your-sns-topic',
      }
  )

- **Describe Job Status:**
  ```python
  glacier.describe_job(
    vaultName='your-vault-name',
    jobId='your-job-id'
  )

- **Get Job Output and Restore Data:**
  ```python
  glacier.get_job_output(
    vaultName='your-vault-name',
    jobId='your-job-id',
    range='bytes=0-1048575'
  )
  s3.put_object(
      Bucket='your-bucket-name',
      Key='your-object-key',
      Body=job_output['body']
  )

- **Delete Archive:**
  ```python
  glacier.delete_archive(
    vaultName='your-vault-name',
    archiveId='your-archive-id'
  )

## Setup

1. Clone the repository:
    ```sh
    git clone https://github.com/DhairyaKarna/Genomics-Cloud-SaaS-Platform.git
    ```
2. Navigate to the project directory:
    ```sh
    cd Genomics-Cloud-SaaS-Platform
    ```
3. Install dependencies (if applicable).

## Configuration

1. **AWS Credentials:**
   Ensure your AWS credentials are configured properly. This can be done by setting up the `~/.aws/credentials` file or using environment variables:
   ```sh
   export AWS_ACCESS_KEY_ID=your_access_key_id
   export AWS_SECRET_ACCESS_KEY=your_secret_access_key

2. **Stripe Credentials:**
   Configure your Stripe credentials by setting up environment variables:
   ```sh
   export STRIPE_API_KEY=your_stripe_api_key

## High-Level User Flow

### Job Submission

1. **User Authentication**: Users access the system through the web interface hosted on the `web` EC2 instance.
2. **Submission and Tracking**: Users submit genomic data for annotation. Job details are stored in DynamoDB and sent to an SQS queue for processing.

### Annotation Processing

1. **Job Retrieval**: The `ann` EC2 instance polls the SQS queue, retrieves job details from DynamoDB, and downloads the input file from S3.
2. **Annotation Execution**: Genomic data is annotated. Results and logs are stored in S3, and job status is updated in DynamoDB.

### Archival and Access Control

1. **Archival Trigger**: For free users, a Step Function waits for 3 minutes post-completion, then sends the job for archival to another SQS queue.
2. **Archival Process**: The `util` EC2 instance archives the result file in Glacier, updates DynamoDB with the archive ID, and cleans up S3 and SQS.

### Result Access

- **Premium Users**: Immediate access to results and logs via the web interface.
- **Free Users**: Results are archived. The web interface offers an upgrade option for result access if requested post-archival.

## Usage

1. Ensure AWS and Stripe credentials are properly configured.
2. Run the application as per the provided scripts or instructions.

## Contributing

Contributions are welcome! Please open an issue or submit a pull request.
