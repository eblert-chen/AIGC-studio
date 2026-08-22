package controller

import (
	"errors"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/model"
	"github.com/stretchr/testify/require"
)

func TestGetResponseBodyRedactsProviderCredentialFromTransportError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	endpoint := server.URL
	server.Close()

	credential := "provider key+/with?reserved&bytes"
	userinfoSecret := "userinfo-secret"
	signedToken := "signed-query-token"
	endpoint = strings.Replace(endpoint, "http://", "http://operator:"+userinfoSecret+"@", 1)
	_, err := GetResponseBody(
		http.MethodGet,
		endpoint+"/balance?api_key="+url.QueryEscape(credential)+"&signed_token="+signedToken,
		&model.Channel{Key: credential},
		http.Header{},
	)
	require.Error(t, err)
	require.NotContains(t, err.Error(), credential)
	require.NotContains(t, err.Error(), url.QueryEscape(credential))
	require.NotContains(t, err.Error(), "api_key")
	require.NotContains(t, err.Error(), userinfoSecret)
	require.NotContains(t, err.Error(), signedToken)

	nested := &url.Error{
		Op:  http.MethodGet,
		URL: "https://operator:" + userinfoSecret + "@provider.invalid/balance?signed_token=" + signedToken,
		Err: &url.Error{
			Op:  http.MethodGet,
			URL: "https://provider.invalid/balance?api_key=" + url.QueryEscape(credential),
			Err: errors.New("connection refused"),
		},
	}
	sanitized := sanitizeFetchModelsError(nested, credential)
	require.EqualError(t, sanitized, "connection refused")
}
