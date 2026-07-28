data "hostinger_vps_data_centers" "all" {}
data "hostinger_vps_templates" "all" {}

resource "hostinger_vps_ssh_key" "agentic_os" {
  name = "agentic-os-hub"
  # pathexpand: Terraform's file() does not expand "~" itself — the default
  # var value is "~/.ssh/id_ed25519.pub", which fails at plan time without this.
  key = file(pathexpand(var.ssh_public_key_path))
}

resource "hostinger_vps_post_install_script" "docker_bootstrap" {
  name    = "agentic-os-bootstrap"
  content = file("${path.module}/../../scripts/bootstrap.sh")
}

resource "hostinger_vps" "hub" {
  plan                   = var.vps_plan
  data_center_id         = var.data_center_id
  template_id            = var.template_id
  hostname               = "agentic-os-hub.local"
  ssh_key_ids            = [hostinger_vps_ssh_key.agentic_os.id]
  post_install_script_id = hostinger_vps_post_install_script.docker_bootstrap.id
}
