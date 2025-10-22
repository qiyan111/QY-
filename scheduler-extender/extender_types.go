package main

import (
	corev1 "k8s.io/api/core/v1"
)

// ExtenderArgs represents the arguments that should be passed to the extender
type ExtenderArgs struct {
	// Pod being scheduled
	Pod corev1.Pod
	// List of candidate nodes where the pod can be scheduled
	Nodes *corev1.NodeList
	// List of candidate node names where the pod can be scheduled
	NodeNames *[]string
}

// ExtenderFilterResult holds the result of a filter call to an extender
type ExtenderFilterResult struct {
	// Filtered set of nodes
	Nodes *corev1.NodeList
	// Filtered set of nodes by name
	NodeNames *[]string
	// Map of failed nodes and failure messages
	FailedNodes map[string]string
	// Error message
	Error string
}

// HostPriority represents the priority of scheduling to a particular host
type HostPriority struct {
	// Name of the host
	Host string
	// Score associated with the host
	Score int64
}
