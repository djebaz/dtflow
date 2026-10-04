#!/usr/bin/env sh
# Regenerates the TEST-ONLY TLS fixtures in this directory with developer OpenSSL (3.x).
# These certificates and keys exist only to exercise strict TLS verification in offline tests.
# Never use them for anything else. CA private keys are discarded after signing.
set -eu
cd "$(dirname "$0")"
DAYS=36500
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

new_ca() { # name common-name
  openssl ecparam -name prime256v1 -genkey -noout -out "$WORK/$1.key"
  cat > "$WORK/$1.cnf" <<EOF
[req]
distinguished_name = dn
x509_extensions = ext
prompt = no
[dn]
CN = $2
[ext]
basicConstraints = critical,CA:TRUE
keyUsage = critical,keyCertSign,cRLSign
subjectKeyIdentifier = hash
EOF
  openssl req -x509 -new -key "$WORK/$1.key" -sha256 -days "$DAYS" -config "$WORK/$1.cnf" -out "$WORK/$1.pem"
}

new_leaf() { # file-stem ca-name san-list
  openssl ecparam -name prime256v1 -genkey -noout -out "$1.key"
  openssl req -new -key "$1.key" -subj "/CN=dtflow-test-$1" -out "$WORK/$1.csr"
  cat > "$WORK/$1.ext" <<EOF
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature
extendedKeyUsage = serverAuth
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid
subjectAltName = $3
EOF
  openssl x509 -req -in "$WORK/$1.csr" -CA "$WORK/$2.pem" -CAkey "$WORK/$2.key" -CAcreateserial \
    -CAserial "$WORK/$2.srl" -sha256 -days "$DAYS" -extfile "$WORK/$1.ext" -out "$1.pem"
}

new_ca ca "DTFlow TEST CA (test only)"
new_ca other "DTFlow TEST untrusted CA (test only)"
cp "$WORK/ca.pem" ca.pem

new_leaf server ca "IP:127.0.0.1,IP:::1,DNS:localhost"
new_leaf dnsonly ca "DNS:dtflow-fixture.test"
new_leaf wrongname ca "DNS:other.example"
new_leaf untrusted other "IP:127.0.0.1,IP:::1,DNS:localhost"

for f in ca server dnsonly wrongname untrusted; do
  printf '%s: ' "$f"; openssl x509 -in "$f.pem" -noout -enddate
done
