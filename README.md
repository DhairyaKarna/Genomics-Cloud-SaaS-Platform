# Dhairya Karna A14 Data Archival GAS Framework
An enhanced web framework (based on [Flask](https://flask.palletsprojects.com/)) for use in the capstone project. Adds robust user authentication (via [Globus Auth](https://docs.globus.org/api/auth)), modular templates, and some simple styling based on [Bootstrap](https://getbootstrap.com/docs/3.3/).

Directory contents are as follows:
* `/web` - The GAS web app files
* `/ann` - Annotator files
* `/util` - Utility scripts/apps for notifications, archival, and restoration
* `/aws` - AWS user data files

## Rationale Behind Archival Workflow

The genomic annotation service employs a sophisticated workflow for handling the archival of result files, particularly for free users. This process is designed with several key objectives in mind:

### Workflow Orchestration

- **AWS Step Functions** is utilized to orchestrate the sequence of operations post-job completion. It allows for the management of conditional logic, such as introducing delays (using the Wait state) before proceeding with the next steps in the workflow, e.g., archiving files for free users.

### Efficient Resource Utilization

- Introducing a **Wait** state before the archival process ensures that compute resources are utilized efficiently. This strategic delay allows the system to prioritize more urgent tasks without incurring unnecessary costs.
- https://aws.amazon.com/step-functions/pricing/

### Decoupling Components

- The use of **SNS (Simple Notification Service)** for publishing messages to an **SQS (Simple Queue Service)** queue ensures the decoupling of the archival process from other system components. This enhances modularity and allows for independent scaling of the archival process.
- https://aws.amazon.com/sns/pricing/

### Scalability and Reliability

- **SQS** ensures reliable message processing, with support for at least once delivery. This facilitates horizontal scaling of the `util` instance, enhancing the system's ability to handle workload spikes efficiently.

### Conditional Processing and Notification

- Conditional logic is easily managed through the use of SNS and Step Functions, enabling the system to specifically target files belonging to free users for archival, after a predefined delay.

## Rationale Behind File Handling and Archival Process

The archive service employs a comprehensive approach to handle and archive result files, especially focusing on optimizing storage management and ensuring data durability. This process involves several AWS services, including Amazon S3 for storage, Amazon Glacier for long-term archival, and the use of specific SDK methods to stream and manage files efficiently. The rationale behind each step of this process is detailed below:

### Streamlined File Access with Amazon S3

- **Amazon S3's `get_object` Method**: The archive service uses this method to retrieve result files stored in S3. This approach is favored for its efficiency in accessing large files without needing to download them locally, thus minimizing the I/O operations and the associated latency.

  - **Streaming Body**: The `StreamingBody` object obtained from `get_object` allows for on-the-fly access to the file's bytes. This is crucial for handling large genomic data files, as it prevents the need for substantial temporary storage and reduces the time to process files.

### Efficient Archival with Amazon Glacier

- **Direct Upload via `glacier.upload_archive`**: By uploading the streaming bytes directly to Amazon Glacier, the service ensures that data is moved to long-term storage without intermediate steps. This method is cost-effective for storing data that is infrequently accessed, aligning with the needs of free users who may not need immediate access to archived results.

  - **Cost-Effectiveness**: Glacier provides a cost-efficient solution for long-term data storage, making it an ideal choice for archiving large genomic datasets. By archiving result files for free users, the service can significantly reduce storage costs while still making data retrievable when needed.
  - https://docs.aws.amazon.com/whitepapers/latest/how-aws-pricing-works/amazon-s3-glacier.html

### Optimization of Storage Costs

- **Deletion with `s3.delete_object`**: After successfully uploading a file to Glacier, the original result file in the S3 `gas-results` bucket is deleted. This step is critical for managing storage costs effectively, as it ensures that S3 is only used for active data processing and short-term storage, while long-term archival is handled in Glacier.

  - **Storage Management**: This practice allows the service to maintain an optimal balance between accessibility and cost. It ensures that storage resources are allocated efficiently, prioritizing quick access for current projects and cost-effective solutions for long-term data retention.



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

## Deployment and Operations

- **Infrastructure**: Deployment scripts and configuration management ensure that the EC2 instances, SQS queues, S3 buckets, and DynamoDB tables are correctly set up.
- **Monitoring and Maintenance**: AWS CloudWatch monitors system health, while routine checks ensure operational integrity and security.

## Conclusion

This genomic annotation service represents a thoughtful application of AWS cloud services and EC2 instances to meet the specific needs of genomic data processing. It balances cost, efficiency, and user experience, providing a scalable solution for both immediate and long-term data handling.
