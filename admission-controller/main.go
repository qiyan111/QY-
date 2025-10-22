package main

import (
    "context"
    "crypto/tls"
    "encoding/json"
    "fmt"
    "io/ioutil"
    "log"
    "net/http"

    slopb "github.com/example/slo-scheduler/proto/api/slo"
    "google.golang.org/grpc"
    admissionv1 "k8s.io/api/admission/v1"
    corev1 "k8s.io/api/core/v1"
    metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

const creditSvcAddr = "credit-service:8081"

func fetchCredit(tenant string) (float64, error) {
    conn, err := grpc.Dial(creditSvcAddr, grpc.WithInsecure())
    if err != nil { return 0, err }
    defer conn.Close()
    client := slopb.NewTenantCreditServiceClient(conn)
    resp, err := client.GetTenantCredit(context.Background(), &slopb.TenantCreditRequest{TenantId: tenant})
    if err != nil { return 0, err }
    return resp.Score, nil
}

func mutatePods(w http.ResponseWriter, r *http.Request) {
    body, err := ioutil.ReadAll(r.Body)
    if err != nil { http.Error(w, err.Error(), 400); return }

    var review admissionv1.AdmissionReview
    if err := json.Unmarshal(body, &review); err != nil {
        http.Error(w, err.Error(), 400); return
    }

    pod := &corev1.Pod{}
    if err := json.Unmarshal(review.Request.Object.Raw, pod); err != nil {
        http.Error(w, err.Error(), 400); return
    }

    tenant := pod.Labels["tenant"]
    credit, _ := fetchCredit(tenant)

    patches := []map[string]interface{}{}
    priority := "low-priority"
    if credit > 0.8 { priority = "high-priority" } else if credit > 0.5 { priority = "medium-priority" }
    patches = append(patches, map[string]interface{}{ "op": "add", "path": "/spec/priorityClassName", "value": priority })

    patchBytes, _ := json.Marshal(patches)

    review.Response = &admissionv1.AdmissionResponse{
        UID: review.Request.UID,
        Allowed: true,
        PatchType: func() *admissionv1.PatchType { pt := admissionv1.PatchTypeJSONPatch; return &pt }(),
        Patch: patchBytes,
    }

    respBytes, _ := json.Marshal(review)
    w.Header().Set("Content-Type", "application/json")
    w.Write(respBytes)
}

func main() {
    mux := http.NewServeMux()
    mux.HandleFunc("/mutate", mutatePods)

    server := &http.Server{
        Addr:      ":8443",
        Handler:   mux,
        TLSConfig: &tls.Config{MinVersion: tls.VersionTLS12},
    }
    log.Println("admission-controller listening on 8443")
    err := server.ListenAndServeTLS("/tls/tls.crt", "/tls/tls.key")
    if err != nil {
        fmt.Println(err)
    }
}
