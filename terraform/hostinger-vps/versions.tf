terraform {
  required_version = ">= 1.7"

  required_providers {
    hostinger = {
      source  = "hostinger/hostinger"
      version = "~> 0.1"
    }
  }
}

provider "hostinger" {
  # Reads HOSTINGER_API_TOKEN from the environment; do not hardcode the token here.
}
