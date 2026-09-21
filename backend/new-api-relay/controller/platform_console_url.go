package controller

import (
	"net"
	"net/url"
	"strings"
)

func platformConsoleURLFromBaseURL(rawBaseURL string) string {
	baseURL := strings.TrimSpace(rawBaseURL)
	if baseURL == "" {
		return ""
	}

	parsed, err := url.Parse(baseURL)
	if err != nil || !parsed.IsAbs() || parsed.Hostname() == "" {
		return ""
	}
	if parsed.Scheme != "https" && parsed.Scheme != "http" {
		return ""
	}
	if parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" {
		return ""
	}
	if parsed.Path != "" && parsed.Path != "/" {
		return ""
	}
	if parsed.Scheme == "http" && !isLoopbackPlatformHost(parsed.Hostname()) {
		return ""
	}

	parsed.Path = "/platform"
	parsed.RawPath = ""
	parsed.ForceQuery = false
	return parsed.String()
}

func isLoopbackPlatformHost(hostname string) bool {
	host := strings.Trim(strings.ToLower(hostname), "[]")
	if host == "localhost" {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}
