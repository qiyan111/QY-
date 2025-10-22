package credit

import (
    slopb "github.com/example/slo-scheduler/proto/api/slo"
    "testing"
)

func TestUpdateViolation(t *testing.T) {
    mgr := NewManager()
    vid := "tenantA"
    mgr.UpdateViolation(&slopb.ViolationRecord{TenantId: vid})
    credit := mgr.GetCredit(vid)
    if credit.Score >= 1.0 {
        t.Fatalf("expected credit to decrease, got %f", credit.Score)
    }
}
