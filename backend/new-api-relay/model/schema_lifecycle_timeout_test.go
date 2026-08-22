package model

import (
	"context"
	"database/sql"
	"database/sql/driver"
	"errors"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

type relayLifecycleBlockingConnector struct {
	connect bool
}

func (connector relayLifecycleBlockingConnector) Connect(ctx context.Context) (driver.Conn, error) {
	if connector.connect {
		<-ctx.Done()
		return nil, ctx.Err()
	}
	return relayLifecycleBlockingConnection{}, nil
}

func (connector relayLifecycleBlockingConnector) Driver() driver.Driver {
	return relayLifecycleBlockingDriver{}
}

type relayLifecycleBlockingDriver struct{}

func (relayLifecycleBlockingDriver) Open(string) (driver.Conn, error) {
	return relayLifecycleBlockingConnection{}, nil
}

type relayLifecycleBlockingConnection struct{}

func (relayLifecycleBlockingConnection) Prepare(string) (driver.Stmt, error) {
	return nil, errors.New("unsupported")
}

func (relayLifecycleBlockingConnection) Close() error { return nil }

func (relayLifecycleBlockingConnection) Begin() (driver.Tx, error) {
	return nil, errors.New("unsupported")
}

func (relayLifecycleBlockingConnection) ExecContext(
	ctx context.Context,
	_ string,
	_ []driver.NamedValue,
) (driver.Result, error) {
	<-ctx.Done()
	return nil, ctx.Err()
}

func TestRelayOfflineLifecycleLockAcquireIsBoundedBeforeMutation(t *testing.T) {
	require.Equal(t, 10*time.Second, relayLifecycleLockAcquireTimeout)
	for _, test := range []struct {
		name            string
		blockConnection bool
		expectedError   string
	}{
		{name: "database connection", blockConnection: true, expectedError: "Relay lifecycle lock connection failed"},
		{name: "advisory lock", expectedError: "Relay lifecycle lock could not be acquired"},
	} {
		t.Run(test.name, func(t *testing.T) {
			database := sql.OpenDB(relayLifecycleBlockingConnector{connect: test.blockConnection})
			t.Cleanup(func() { _ = database.Close() })
			started := time.Now()
			lock, err := acquireRelayLifecycleLockFromSQLDBWithTimeout(
				context.Background(), database, false, 25*time.Millisecond,
			)
			require.Nil(t, lock)
			require.EqualError(t, err, test.expectedError)
			require.Less(t, time.Since(started), time.Second)
		})
	}
}
