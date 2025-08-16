#!/usr/bin/env python3
"""
Script de déploiement Vultr multi-région et test de latence vers CEX/DEX
Requis: pip install vultr-python requests aiohttp asyncio pandas rich
"""

import os
import json
import time
import asyncio
import aiohttp
import requests
import pandas as pd
from datetime import datetime
from typing import Dict, List, Tuple
import subprocess
import logging
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv
import base64

# Charger les variables depuis .env local (si présent)
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"), override=False)

# Configuration
VULTR_API_KEY = os.getenv("VULTR_API_KEY")  # Export your API key
# Optional: attach Vultr SSH key(s) at instance creation (per Vultr API)
VULTR_SSH_KEY_ID = os.getenv("VULTR_SSH_KEY_ID", "").strip()
VULTR_SSH_KEY_IDS = os.getenv("VULTR_SSH_KEY_IDS", "").strip()  # comma-separated
# Optional: local SSH private key to access instances (adds -i to ssh/scp)
SSH_KEY_PATH = os.getenv("SSH_KEY_PATH", "").strip()
# Optional: inject a public key via user_data as fallback
SSH_PUBLIC_KEY = os.getenv("SSH_PUBLIC_KEY", "").strip()
SSH_PUBLIC_KEY_PATH = os.getenv("SSH_PUBLIC_KEY_PATH", "").strip()
# SSH connection timeout (seconds)
SSH_CONNECT_TIMEOUT = int(os.getenv("SSH_CONNECT_TIMEOUT", "15"))

# Logging basique (console + fichier)
logger = logging.getLogger("vultr-latency")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    _formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    _console = logging.StreamHandler()
    _console.setLevel(logging.INFO)
    _console.setFormatter(_formatter)
    _file = RotatingFileHandler("launch-gpt5.log", maxBytes=1_000_000, backupCount=3)
    _file.setLevel(logging.INFO)
    _file.setFormatter(_formatter)
    logger.addHandler(_console)
    logger.addHandler(_file)

# Mapping Région -> Exchanges
REGION_EXCHANGE_MAP = {
    "nrt": {  # Tokyo
        "name": "Tokyo",
        "cex": {
            "binance": "https://api.binance.com/api/v3/ping",
            "okx": "https://www.okx.com/api/v5/public/time",
            "bitflyer": "https://api.bitflyer.com/v1/getmarkets",
            "gmo": "https://api.coin.z.com/public/v1/status"
        },
        "dex": {
            "sushiswap": "https://api.sushi.com/",
            "uniswap": "https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3"
        }
    },
    "sgp": {  # Singapore
        "name": "Singapore", 
        "cex": {
            "bybit": "https://api.bybit.com/v5/market/time",
            "kucoin": "https://api.kucoin.com/api/v1/timestamp",
            "crypto.com": "https://api.crypto.com/v2/public/get-ticker",
            "huobi": "https://api.huobi.pro/v1/common/timestamp"
        },
        "dex": {
            "pancakeswap": "https://api.thegraph.com/subgraphs/name/pancakeswap/exchange",
            "dydx": "https://api.dydx.exchange/v3/candles/BTC-USD"
        }
    },
    "fra": {  # Frankfurt
        "name": "Frankfurt",
        "cex": {
            "kraken": "https://api.kraken.com/0/public/Time",
            "bitstamp": "https://www.bitstamp.net/api/v2/ticker/btcusd/",
            "bitfinex": "https://api-pub.bitfinex.com/v2/platform/status"
        },
        "dex": {
            "uniswap": "https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3",
            "curve": "https://api.curve.fi/api/getPools"
        }
    },
    "ewr": {  # New York
        "name": "New York",
        "cex": {
            "coinbase": "https://api.coinbase.com/v2/time",
            "gemini": "https://api.gemini.com/v1/pubticker/btcusd",
            "kraken": "https://api.kraken.com/0/public/Time"
        },
        "dex": {
            "uniswap": "https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3"
        }
    },
    "icn": {  # Seoul
        "name": "Seoul",
        "cex": {
            "upbit": "https://api.upbit.com/v1/ticker?markets=KRW-BTC",
            "bithumb": "https://api.bithumb.com/public/ticker/BTC_KRW",
            "korbit": "https://api.korbit.co.kr/v1/ticker?currency_pair=btc_krw"
        },
        "dex": {
            "klayswap": "https://s.klayswap.com/stat/klayswapInfo.json"
        }
    }
}

# Configuration Vultr 
VULTR_PLAN_ID = "vc2-1c-2gb"  # $12/month plan (upgradeable)
VULTR_OS_ID = 1743  # Ubuntu 22.04 LTS

class VultrDeployer:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.vultr.com/v2"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        self.instances = {}
        
    def get_regions(self) -> Dict:
        """Récupère la liste des régions disponibles"""
        response = requests.get(
            f"{self.base_url}/regions",
            headers=self.headers
        )
        if response.ok:
            return response.json()
        logger.error(f"Erreur API get_regions: {response.status_code} {response.text}")
        return {}
    
    def create_instance(self, region: str, label: str) -> str:
        """Crée une instance dans une région spécifique"""
        # Build optional SSH public key injection block (fallback)
        public_key_content = SSH_PUBLIC_KEY
        if not public_key_content and SSH_PUBLIC_KEY_PATH and os.path.exists(SSH_PUBLIC_KEY_PATH):
            try:
                with open(SSH_PUBLIC_KEY_PATH, 'r') as pkf:
                    public_key_content = pkf.read().strip()
            except Exception as e:
                logger.error(f"Erreur lecture SSH_PUBLIC_KEY_PATH: {e}")

        public_key_block = ""
        if public_key_content:
            public_key_block = f"""
mkdir -p /root/.ssh
chmod 700 /root/.ssh
echo '{public_key_content}' >> /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
chown -R root:root /root/.ssh
"""

        startup_script = """#!/bin/bash
apt-get update
apt-get install -y python3-pip curl net-tools
pip3 install aiohttp requests pandas numpy
""" + public_key_block + """
cat > /root/latency_test.py << 'EOF'
import asyncio
import aiohttp
import time
import json

async def test_latency(url, session):
    try:
        start = time.time()
        async with session.get(url, timeout=5) as response:
            await response.text()
            return (time.time() - start) * 1000
    except:
        return -1

async def main():
    urls = json.loads(open('/root/endpoints.json').read())
    async with aiohttp.ClientSession() as session:
        results = {}
        for name, url in urls.items():
            latencies = []
            for _ in range(10):
                lat = await test_latency(url, session)
                if lat > 0:
                    latencies.append(lat)
                await asyncio.sleep(0.1)
            if latencies:
                results[name] = {
                    'min': min(latencies),
                    'avg': sum(latencies)/len(latencies),
                    'max': max(latencies)
                }
        print(json.dumps(results, indent=2))

asyncio.run(main())
EOF
"""
        # Vultr API attend un user_data encodé en base64
        encoded_user_data = base64.b64encode(startup_script.encode("utf-8")).decode("ascii")
        
        data = {
            "region": region,
            "plan": VULTR_PLAN_ID,
            "os_id": VULTR_OS_ID,
            "label": label,
            "hostname": label,
            "enable_ipv6": True,
            "user_data": encoded_user_data,
            "backups": "disabled"
        }

        # Attach SSH key(s) via Vultr API if provided (expects an array: sshkey_ids)
        ssh_ids = []
        if VULTR_SSH_KEY_IDS:
            ssh_ids = [s.strip() for s in VULTR_SSH_KEY_IDS.split(',') if s.strip()]
        elif VULTR_SSH_KEY_ID:
            ssh_ids = [VULTR_SSH_KEY_ID]
        if ssh_ids:
            data["sshkey_ids"] = ssh_ids
            logger.info(f"Création instance {region}: sshkey_ids={ssh_ids}")
        
        response = requests.post(
            f"{self.base_url}/instances",
            headers=self.headers,
            json=data
        )
        
        if response.status_code == 202:
            instance_data = response.json()
            instance_id = instance_data['instance']['id']
            self.instances[region] = instance_id
            return instance_id
        else:
            logger.error(f"Erreur création instance {region}: {response.status_code} {response.text}")
            return None
    
    def get_instance_info(self, instance_id: str) -> Dict:
        """Récupère les infos d'une instance"""
        response = requests.get(
            f"{self.base_url}/instances/{instance_id}",
            headers=self.headers
        )
        if response.ok:
            return response.json()['instance']
        logger.error(f"Erreur API get_instance_info: {response.status_code} {response.text}")
        return {}
    
    def wait_for_instances(self, timeout: int = 300):
        """Attend que toutes les instances soient prêtes"""
        start_time = time.time()
        ready = {}
        
        while len(ready) < len(self.instances) and (time.time() - start_time) < timeout:
            for region, instance_id in self.instances.items():
                if region not in ready:
                    info = self.get_instance_info(instance_id)
                    if info['status'] == 'active' and info['power_status'] == 'running':
                        ready[region] = info['main_ip']
                        print(f"✅ {region} prêt: {info['main_ip']}")
            
            if len(ready) < len(self.instances):
                time.sleep(10)
        
        return ready
    
    def destroy_instance(self, instance_id: str):
        """Détruit une instance"""
        response = requests.delete(
            f"{self.base_url}/instances/{instance_id}",
            headers=self.headers
        )
        if response.status_code != 204:
            logger.error(f"Erreur suppression instance {instance_id}: {response.status_code} {response.text}")
        return response.status_code == 204

class LatencyTester:
    def __init__(self, instances_ips: Dict):
        self.instances = instances_ips
        self.results = {}
        self.identity_opt = f"-i {SSH_KEY_PATH}" if SSH_KEY_PATH else ""
        self.common_opts = f"-o StrictHostKeyChecking=no -o BatchMode=yes -o ConnectTimeout={SSH_CONNECT_TIMEOUT}"

    def _wait_for_ssh(self, ip: str, retries: int = 10, delay_s: float = 6.0) -> bool:
        """Attend que le port SSH accepte la connexion clé (tentatives limitées)."""
        for attempt in range(1, retries + 1):
            cmd = f"ssh {self.common_opts} {self.identity_opt} root@{ip} 'echo ok'"
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            if res.returncode == 0 and res.stdout.strip() == 'ok':
                return True
            logger.info(f"SSH pas prêt sur {ip} (tentative {attempt}/{retries}) code={res.returncode}")
            time.sleep(delay_s)
        logger.error(f"SSH indisponible sur {ip} après {retries} tentatives")
        return False
    
    async def test_endpoint(self, session, url: str, name: str) -> Tuple[str, float]:
        """Test un endpoint unique"""
        try:
            start = time.perf_counter()
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as response:
                await response.text()
                latency = (time.perf_counter() - start) * 1000
                return (name, latency)
        except Exception as e:
            logger.debug(f"Erreur test_endpoint {name}: {e}")
            return (name, -1)
    
    async def test_from_region(self, region: str, ip: str, endpoints: Dict) -> Dict:
        """Test depuis une région spécifique"""
        # Copie les endpoints vers l'instance
        with open('/tmp/endpoints.json', 'w') as f:
            json.dump(endpoints, f)
        
        # Options SSH communes (BatchMode pour éviter les prompts, timeout pour éviter les blocages)
        identity_opt = self.identity_opt
        common_opts = self.common_opts

        # Attendre SSH prêt
        if not self._wait_for_ssh(ip):
            return {}

        # SCP le fichier vers l'instance
        scp_cmd = f"scp {common_opts} {identity_opt} /tmp/endpoints.json root@{ip}:/root/"
        scp_res = subprocess.run(scp_cmd, shell=True, capture_output=True, text=True)
        if scp_res.returncode != 0:
            logger.error(f"SCP échec vers {ip}: code={scp_res.returncode} stderr={scp_res.stderr.strip()}")
            return {}
        
        # Execute le test sur l'instance distante
        ssh_cmd = f"ssh {common_opts} {identity_opt} root@{ip} 'python3 /root/latency_test.py'"
        result = subprocess.run(ssh_cmd, shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error(f"SSH échec sur {ip}: code={result.returncode} stderr={result.stderr.strip()}")
            return {}
        
        try:
            return json.loads(result.stdout)
        except:
            return {}
    
    async def test_all_regions(self) -> pd.DataFrame:
        """Test toutes les régions et compile les résultats"""
        all_results = []
        
        for region, ip in self.instances.items():
            if region in REGION_EXCHANGE_MAP:
                print(f"\n🔍 Test depuis {REGION_EXCHANGE_MAP[region]['name']} ({region})...")
                # Combine CEX et DEX endpoints
                endpoints = {}
                endpoints.update(REGION_EXCHANGE_MAP[region].get('cex', {}))
                endpoints.update(REGION_EXCHANGE_MAP[region].get('dex', {}))
                # Test distant (depuis l'instance)
                remote_results = await self.test_from_region(region, ip, endpoints)
                for exchange, stats in remote_results.items():
                    if isinstance(stats, dict) and 'avg' in stats:
                        all_results.append({
                            'Region': REGION_EXCHANGE_MAP[region]['name'],
                            'Exchange': exchange,
                            'Type': 'CEX' if exchange in REGION_EXCHANGE_MAP[region].get('cex', {}) else 'DEX',
                            'Latency (ms)': round(float(stats['avg']), 2),
                            'Timestamp': datetime.now()
                        })
        
        return pd.DataFrame(all_results)

async def main():
    print("🚀 Démarrage du déploiement Vultr multi-région...")
    
    # Initialiser le deployer
    deployer = VultrDeployer(VULTR_API_KEY)
    
    # Sélectionner les régions à déployer
    regions_to_deploy = ["nrt", "sgp", "fra", "ewr", "icn"]
    
    print(f"\n📍 Déploiement dans {len(regions_to_deploy)} régions...")
    
    # Créer les instances
    for region in regions_to_deploy:
        label = f"arb-test-{region}-{int(time.time())}"
        instance_id = deployer.create_instance(region, label)
        if instance_id:
            print(f"  ✓ Instance créée dans {region}: {instance_id}")
        else:
            print(f"  ✗ Échec création dans {region}")
    
    # Attendre que les instances soient prêtes
    print("\n⏳ Attente du démarrage des instances (2-3 minutes)...")
    instances_ips = deployer.wait_for_instances()
    
    # Choix de la durée de test
    print("\n⏲️  Sélectionnez la durée du test: 0 (single pass), 1, 5, 15, 60 minutes (1h) — Entrée pour 5 par défaut")
    duration_choice = input("Durée (min) [0/1/5/15/60/1h]: ").strip().lower()
    valid_choices = {"": 5, "0": 0, "1": 1, "1m": 1, "5": 5, "15": 15, "60": 60, "1h": 60, "h": 60}
    test_minutes = valid_choices.get(duration_choice, 5)
    if test_minutes == 0:
        print("⏳ Single-pass test (one iteration per geography)...")
    else:
        print(f"⏳ Test en cours pour {test_minutes} minutes...")

    # Tester les latences sur la durée choisie
    tester = LatencyTester(instances_ips)
    aggregated_results: List[pd.DataFrame] = []
    if test_minutes == 0:
        # Single pass
        try:
            run_df = await tester.test_all_regions()
            if not run_df.empty:
                aggregated_results.append(run_df)
        except Exception as e:
            logger.error(f"Erreur pendant la mesure unique: {e}")
    else:
        start_ts = time.time()
        iteration = 0
        while time.time() - start_ts < test_minutes * 60:
            iteration += 1
            print(f"\n📊 Mesure {iteration}...")
            try:
                run_df = await tester.test_all_regions()
                if not run_df.empty:
                    aggregated_results.append(run_df)
            except Exception as e:
                logger.error(f"Erreur pendant la mesure {iteration}: {e}")
            await asyncio.sleep(30)  # intervalle entre mesures
    
    # Agréger et afficher les résultats
    print("\n" + "="*80)
    print("RÉSULTATS DES TESTS DE LATENCE")
    print("="*80)
    
    final_df = pd.concat(aggregated_results, ignore_index=True) if aggregated_results else pd.DataFrame()

    # Tableau récapitulatif par région
    if not final_df.empty:
        pivot_table = final_df.pivot_table(
            values='Latency (ms)',
            index='Exchange',
            columns='Region',
            aggfunc='mean'
        ).round(2)
        print("\n📈 Latences moyennes (ms):")
        print(pivot_table.to_string())
    else:
        print("\n⚠️  Aucun résultat agrégé à afficher.")
    
    # Top 10 meilleures latences
    print("\n🏆 Top 10 meilleures latences:")
    if not final_df.empty and 'Latency (ms)' in final_df.columns:
        best_latencies = final_df.nsmallest(10, 'Latency (ms)')
        required_cols = ['Region', 'Exchange', 'Type', 'Latency (ms)']
        if all(col in best_latencies.columns for col in required_cols):
            print(best_latencies[required_cols].to_string(index=False))
        else:
            print(best_latencies.to_string(index=False))
    else:
        print("⚠️  Aucun résultat disponible pour un Top 10.")
    
    # Sauvegarder les résultats
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = f"vultr_latency_test_{timestamp}_{test_minutes}m.csv"
    if not final_df.empty:
        final_df.to_csv(out_file, index=False)
        print(f"\n💾 Résultats sauvegardés: {out_file}")
    else:
        print("\n⚠️  Aucun résultat à sauvegarder.")
    
    # Calculer les coûts
    print("\n💰 Estimation des coûts:")
    test_hours = test_minutes / 60.0
    print(f"  - Test (~{test_minutes} min): ${len(instances_ips) * 0.018 * test_hours:.2f}")
    print(f"  - Mensuel (si gardé): ${len(instances_ips) * 12:.2f}")
    
    # Destruction des instances
    if test_minutes == 0:
        print("\n🗑️  Option 0 sélectionnée: destruction immédiate des instances...")
        for region, instance_id in deployer.instances.items():
            if deployer.destroy_instance(instance_id):
                print(f"  ✓ Instance {region} détruite")
            else:
                print(f"  ✗ Erreur destruction {region}")
    else:
        # Demander si on détruit les instances (auto 'y' après 30s)
        prompt = "\n🗑️  Détruire les instances de test? (y/n) [auto 'y' après 30s]: "
        try:
            destroy = await asyncio.wait_for(asyncio.to_thread(input, prompt), timeout=30)
        except asyncio.TimeoutError:
            print("⏱️  Pas de réponse après 30s, destruction automatique des instances...")
            destroy = 'y'

        if destroy.lower() == 'y':
            for region, instance_id in deployer.instances.items():
                if deployer.destroy_instance(instance_id):
                    print(f"  ✓ Instance {region} détruite")
                else:
                    print(f"  ✗ Erreur destruction {region}")
        else:
            print("  ⚠️  Instances conservées à votre demande.")
    
    print("\n✅ Test terminé!")

if __name__ == "__main__":
    if not VULTR_API_KEY:
        print("❌ Erreur: Variable VULTR_API_KEY non définie")
        print("   Export: export VULTR_API_KEY='your-api-key'")
    else:
        asyncio.run(main())