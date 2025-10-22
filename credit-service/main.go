package main

import (
    "context"
    "log"
    "net"

    slopb "github.com/example/slo-scheduler/proto/api/slo"
    "google.golang.org/grpc"
    "github.com/example/slo-scheduler/credit-service/credit"
    emptypb "google.golang.org/protobuf/types/known/emptypb"
)

// creditServer implements the TenantCreditService gRPC interface (to be generated).
type creditServer struct{
    slopb.UnimplementedTenantCreditServiceServer
    mgr *credit.Manager
}

func (s *creditServer) GetTenantCredit(ctx context.Context, req *slopb.TenantCreditRequest) (*slopb.TenantCredit, error) {
    return s.mgr.GetCredit(req.TenantId), nil
}

func (s *creditServer) RecordViolation(ctx context.Context, v *slopb.ViolationRecord) (*emptypb.Empty, error) {
    s.mgr.UpdateViolation(v)
    return &emptypb.Empty{}, nil
}

func main() {
    lis, err := net.Listen("tcp", ":8081")
    if err != nil {
        log.Fatalf("failed to listen: %v", err)
    }
    grpcServer := grpc.NewServer()
    mgr := credit.NewManager()
    slopb.RegisterTenantCreditServiceServer(grpcServer, &creditServer{mgr: mgr})
    log.Println("credit-service listening on :8081")
    if err := grpcServer.Serve(lis); err != nil {
        log.Fatalf("failed to serve: %v", err)
    }
}
