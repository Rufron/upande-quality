#!/bin/bash
# Mirrors the ERPNext CI install flow: clone Frappe, init a bench, create the
# test DB, pull the dependency apps and this app, then reinstall + install-app.
# Reproduces a real deploy.
#
# Dependency chain: upande_quality -> upande_packhouse -> (upande_core,
# upande_agriculture) -> erpnext -> payments -> frappe. upande_quality ships a
# Custom Field on packhouse's Farm Distance and calls packhouse's stock_movement /
# transfer_control at runtime, so packhouse (and what packhouse itself needs) is
# installed first — in the same order as upande_packhouse's own CI.
#
# Branches/repos are overridable via FRAPPE_BRANCH / ERPNEXT_BRANCH /
# PAYMENTS_BRANCH and the UPANDE_*_REPO / UPANDE_*_BRANCH variables.
#
# The DB credentials (root / test_frappe / admin) are the same throwaway values
# ERPNext and HRMS use in their own CI — the MariaDB service is created fresh
# for the run and torn down after it, so nothing here is a real secret.

set -e

cd ~ || exit

sudo apt update
sudo apt remove -y mysql-server mysql-client || true
sudo apt install -y libcups2-dev redis-server mariadb-client libmariadb-dev pkg-config

pip install frappe-bench

frappeuser=${FRAPPE_USER:-"frappe"}
frappebranch=${FRAPPE_BRANCH:-"version-16"}
erpnextbranch=${ERPNEXT_BRANCH:-"version-16"}
paymentsbranch=${PAYMENTS_BRANCH:-"version-16"}

git clone "https://github.com/${frappeuser}/frappe" --branch "${frappebranch}" --depth 1
bench init --skip-assets --frappe-path ~/frappe --python "$(which python)" frappe-bench

mkdir ~/frappe-bench/sites/test_site
cp -r "${GITHUB_WORKSPACE}/.github/helper/site_config.json" ~/frappe-bench/sites/test_site/

mariadb --host 127.0.0.1 --port 3306 -u root -proot -e "SET GLOBAL character_set_server = 'utf8mb4'"
mariadb --host 127.0.0.1 --port 3306 -u root -proot -e "SET GLOBAL collation_server = 'utf8mb4_unicode_ci'"

mariadb --host 127.0.0.1 --port 3306 -u root -proot -e "CREATE USER 'test_frappe'@'localhost' IDENTIFIED BY 'test_frappe'"
mariadb --host 127.0.0.1 --port 3306 -u root -proot -e "CREATE DATABASE test_frappe"
mariadb --host 127.0.0.1 --port 3306 -u root -proot -e "GRANT ALL PRIVILEGES ON \`test_frappe\`.* TO 'test_frappe'@'localhost'"

mariadb --host 127.0.0.1 --port 3306 -u root -proot -e "FLUSH PRIVILEGES"

install_wkhtmltopdf() {
    wget -O /tmp/wkhtmltox.tar.xz https://github.com/frappe/wkhtmltopdf/raw/master/wkhtmltox-0.12.3_linux-generic-amd64.tar.xz
    tar -xf /tmp/wkhtmltox.tar.xz -C /tmp
    sudo mv /tmp/wkhtmltox/bin/wkhtmltopdf /usr/local/bin/wkhtmltopdf
    sudo chmod o+x /usr/local/bin/wkhtmltopdf
}
install_wkhtmltopdf &

cd ~/frappe-bench || exit

sed -i 's/watch:/# watch:/g' Procfile
sed -i 's/schedule:/# schedule:/g' Procfile
sed -i 's/socketio:/# socketio:/g' Procfile
sed -i 's/redis_socketio:/# redis_socketio:/g' Procfile

bench get-app "https://github.com/${frappeuser}/payments" --branch "$paymentsbranch"
bench get-app "https://github.com/${frappeuser}/erpnext" --branch "$erpnextbranch" --resolve-deps
bench get-app "${UPANDE_CORE_REPO:-https://github.com/upandeltd/Upande-Core.git}" --branch "${UPANDE_CORE_BRANCH:-main}"
bench get-app "${UPANDE_AGRICULTURE_REPO:-https://github.com/Jimmypaps001/upande-agriculture}" --branch "${UPANDE_AGRICULTURE_BRANCH:-develop}"
bench get-app "${UPANDE_PACKHOUSE_REPO:-https://github.com/mark-judah/upande-packhouse-frappe}" --branch "${UPANDE_PACKHOUSE_BRANCH:-develop}"
bench get-app upande_quality "${GITHUB_WORKSPACE}"
bench setup requirements --dev

bench start &>> ~/frappe-bench/bench_start.log &
CI=Yes bench build --app frappe &
bench --site test_site reinstall --yes

# Order matters: core (masters) -> packhouse -> agriculture (Links at packhouse's
# Bucket QR Code) -> quality (Custom Field on packhouse's Farm Distance).
bench --verbose --site test_site install-app upande_core
bench --verbose --site test_site install-app upande_packhouse
bench --verbose --site test_site install-app upande_agriculture
bench --verbose --site test_site install-app upande_quality
