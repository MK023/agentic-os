variable "vps_plan" {
  type        = string
  description = "Hostinger VPS plan identifier (see: terraform console -> data.hostinger_vps_plans.all)"
  default     = "hostingercom-vps-kvm2-usd-1m"
}

variable "data_center_id" {
  type        = number
  description = "Hostinger data center ID. Pick an EU location (data residency). Look up via data.hostinger_vps_data_centers.all before setting."
}

variable "template_id" {
  type        = number
  description = "OS template ID for 'Ubuntu 24.04 with Docker'. Look up via data.hostinger_vps_templates.all before setting."
}

variable "ssh_public_key_path" {
  type        = string
  description = "Path to the local SSH public key to attach to the VPS"
  default     = "~/.ssh/id_ed25519.pub"
}
