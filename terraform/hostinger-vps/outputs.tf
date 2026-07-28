output "vps_ip" {
  value       = hostinger_vps.hub.ipv4_address
  description = "Public IPv4 of the hub VPS. Used only for the initial SSH bootstrap check — no service is exposed directly on this IP."
}
