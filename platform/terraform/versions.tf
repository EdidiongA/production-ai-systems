terraform {
  required_version = ">= 1.9.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }

  # Remote state: uncomment after creating the bucket + lock table once
  # (bootstrap/README.md). Kept commented so `terraform init -backend=false`
  # validates cleanly with zero AWS access.
  #
  # backend "s3" {
  #   bucket         = "waifem-ml-platform-tfstate"
  #   key            = "platform/terraform.tfstate"
  #   region         = "eu-west-1"
  #   dynamodb_table = "waifem-ml-platform-tflock"
  #   encrypt        = true
  # }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "ml-platform"
      ManagedBy = "terraform"
      Owner     = "edidiong"
    }
  }
}
