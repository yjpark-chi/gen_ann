# gen_ann

This repo contains the main files of my capstone project for a cloud computing course.
The three directories represent three different servers hosted on AWS.
* ann: annotates genomics data by processing requests from a message queue
* web: hosts the endpoints for requesting annotation jobs and managing user settings
* util: manages data archival and email notifications

This project will not run as is, as it requires AWS resources.
The project ran on 3 AWS EC2 instances, one for each directory (web, ann, util).
