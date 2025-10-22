package main

import (
    "context"
    "log"
    "time"

    cg "github.com/containerd/cgroups/v3"
    slopb "github.com/example/slo-scheduler/proto/api/slo"
    "google.golang.org/grpc"
)

const (
    creditSvcAddr = "credit-service:8081"
)

func main() {
    log.Println("cgroup-adjuster started (stub)")

    for {
        adjustOnce()
        time.Sleep(30 * time.Second)
    }
}

func adjustOnce() {
    // TODO: list running pods and their cgroups, fetch credit score and adjust CPU quotas.
    conn, err := grpc.Dial(creditSvcAddr, grpc.WithInsecure())
    if err != nil { log.Printf("grpc dial: %v", err); return }
    defer conn.Close()
    client := slopb.NewTenantCreditServiceClient(conn)
    ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
    defer cancel()

    // demo tenant id.
    resp, err := client.GetTenantCredit(ctx, &slopb.TenantCreditRequest{TenantId: "demo"})
    if err != nil { log.Printf("get credit: %v", err); return }

    quota := uint64(100000) // default 100ms period 100% CPU
    if resp.Score < 0.5 {
        quota = 50000 // limit CPU if low credit
    }
    // TODO: find cgroup path of demo tenant pod/container
    _ = cg // silence unused import until logic implemented
    log.Printf("would set quota=%d for tenant demo (score %.2f)", quota, resp.Score)
}
