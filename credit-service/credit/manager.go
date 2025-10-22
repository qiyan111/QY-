package credit

import (
    "sync"
    "time"

    slopb "github.com/example/slo-scheduler/proto/api/slo"
)

type TenantState struct {
    Target   *slopb.TenantSLO
    Credit   *slopb.TenantCredit
}

type Manager struct {
    mu      sync.RWMutex
    tenants map[string]*TenantState
}

func NewManager() *Manager {
    return &Manager{tenants: make(map[string]*TenantState)}
}

// UpdateViolation subtracts credit when a violation occurs.
func (m *Manager) UpdateViolation(v *slopb.ViolationRecord) {
    m.mu.Lock()
    defer m.mu.Unlock()
    st, ok := m.tenants[v.TenantId]
    if !ok {
        // Initialise with default credit 1.0
        st = &TenantState{Credit: &slopb.TenantCredit{TenantId: v.TenantId, Score: 1.0, BudgetRemaining: 1.0}}
        m.tenants[v.TenantId] = st
    }
    // If SLO is not set, we don't penalise
    if st.Target == nil {
        return
    }
    dec := 0.05 // fixed decrement for now
    st.Credit.Score -= dec
    if st.Credit.Score < 0 {
        st.Credit.Score = 0
    }
    st.Credit.UpdateTs = time.Now().UnixMilli()
}

// UpdateSLO sets or updates the SLO for a tenant.
func (m *Manager) UpdateSLO(slo *slopb.TenantSLO) {
    m.mu.Lock()
    defer m.mu.Unlock()
    st, ok := m.tenants[slo.TenantId]
    if !ok {
        st = &TenantState{Credit: &slopb.TenantCredit{TenantId: slo.TenantId, Score: 1.0, BudgetRemaining: 1.0}}
        m.tenants[slo.TenantId] = st
    }
    st.Target = slo
}

func (m *Manager) GetCredit(id string) *slopb.TenantCredit {
    m.mu.RLock()
    defer m.mu.RUnlock()
    if st, ok := m.tenants[id]; ok {
        return st.Credit
    }
    return &slopb.TenantCredit{TenantId: id, Score: 1.0, BudgetRemaining: 1.0}
}

func (m *Manager) GetSLO(id string) *slopb.TenantSLO {
    m.mu.RLock()
    defer m.mu.RUnlock()
    if st, ok := m.tenants[id]; ok {
        return st.Target
    }
    return nil
}
