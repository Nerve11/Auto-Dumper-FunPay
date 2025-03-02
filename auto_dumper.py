#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import time
import logging
import json
import schedule
from dotenv import load_dotenv
import telegram
import sys
import funpayapi
from funpayapi import Account

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("auto_dumper.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Configuration (can be moved to config.json later)
CONFIG = {
    "check_interval_minutes": 10,
    "price_decrease_amount": 1,  # ₽
    "min_price": 0,  # Will be set from config file
    "telegram_token": os.getenv("TELEGRAM_TOKEN"),
    "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID"),
    "funpay_username": os.getenv("FUNPAY_USERNAME"),
    "funpay_password": os.getenv("FUNPAY_PASSWORD"),
    "game_id": "",  # Будет установлено из файла конфигурации
    "server_id": "",  # Будет установлено из файла конфигурации
    "lot_id": "",  # Будет установлено из файла конфигурации
    "whitelist": []  # Будет установлено из файла конфигурации
}

class PriceDumper:
    def __init__(self, config):
        self.config = config
        self.bot = None
        self.funpay_account = None
        
        # Initialize Telegram bot if credentials are provided
        if config["telegram_token"] and config["telegram_chat_id"]:
            self.bot = telegram.Bot(token=config["telegram_token"])
            logger.info("Telegram bot initialized")
        else:
            logger.warning("Telegram credentials not provided, notifications disabled")
    
    def load_config_file(self, config_path="config.json"):
        """Load configuration from a JSON file"""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                user_config = json.load(f)
                
            # Update config with values from file
            for key, value in user_config.items():
                if key in self.config:
                    self.config[key] = value
            
            logger.info(f"Configuration loaded from {config_path}")
            
            # Validate required settings
            required_fields = ["game_id", "server_id", "lot_id", "min_price"]
            for field in required_fields:
                if not self.config[field]:
                    logger.error(f"Required configuration '{field}' is missing or empty")
                    return False
            
            return True
        except Exception as e:
            logger.error(f"Failed to load configuration: {e}")
            return False
    
    def send_telegram_notification(self, message):
        """Send a notification to Telegram"""
        if not self.bot:
            logger.warning("Telegram bot not initialized, skipping notification")
            return False
        
        try:
            self.bot.send_message(chat_id=self.config["telegram_chat_id"], text=message)
            logger.info("Telegram notification sent")
            return True
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")
            return False
    
    def login_to_funpay(self):
        """Log in to FunPay using credentials"""
        try:
            if not self.config["funpay_username"] or not self.config["funpay_password"]:
                logger.error("FunPay credentials not provided")
                return False
            
            # Авторизация через FunPayAPI
            self.funpay_account = Account(
                self.config["funpay_username"],
                self.config["funpay_password"]
            )
            
            logger.info(f"Successfully logged in to FunPay as {self.config['funpay_username']}")
            return True
        except Exception as e:
            logger.error(f"Failed to log in to FunPay: {e}")
            return False
    
    def get_market_prices(self):
        """
        Получить список всех продавцов и цен для определенного лота
        Возвращает список словарей с seller_id, seller_name и price
        """
        try:
            if not self.funpay_account:
                logger.error("Not logged in to FunPay")
                return []
            
            # Получаем все лоты для указанной игры и сервера
            game_id = self.config["game_id"]
            server_id = self.config["server_id"]
            
            # Используем FunPayAPI для получения списка лотов
            all_lots = self.funpay_account.get_lots(game_id, server_id)
            
            sellers = []
            for lot in all_lots:
                # Извлекаем данные о продавце и цене
                sellers.append({
                    'seller_id': lot.user_id,  # ID продавца
                    'seller_name': lot.user_name,  # Имя продавца
                    'price': float(lot.price),  # Цена
                    'lot_id': lot.id  # ID лота
                })
            
            logger.info(f"Found {len(sellers)} sellers on the market")
            return sellers
            
        except Exception as e:
            logger.error(f"Failed to get market prices: {e}")
            return []
    
    def find_my_listing(self, sellers):
        """Find my listing among all sellers"""
        my_lot_id = self.config["lot_id"]
        for seller in sellers:
            if seller['lot_id'] == my_lot_id:
                return seller
        return None
    
    def find_cheapest_competitor(self, sellers):
        """Find the cheapest competitor not in the whitelist"""
        cheapest = None
        my_lot_id = self.config["lot_id"]
        
        for seller in sellers:
            # Skip if it's my listing
            if seller['lot_id'] == my_lot_id:
                continue
                
            # Skip if in whitelist
            if seller['seller_id'] in self.config["whitelist"] or seller['seller_name'] in self.config["whitelist"]:
                continue
                
            if cheapest is None or seller['price'] < cheapest['price']:
                cheapest = seller
                
        return cheapest
    
    def update_my_price(self, new_price):
        """
        Update my price on FunPay using API
        """
        try:
            if not self.funpay_account:
                logger.error("Not logged in to FunPay")
                return False
            
            # Получаем ID лота
            lot_id = self.config["lot_id"]
            
            # Используем API для обновления цены
            self.funpay_account.change_lot_price(lot_id, new_price)
            
            logger.info(f"Price updated to {new_price}₽")
            return True
            
        except Exception as e:
            logger.error(f"Failed to update price: {e}")
            return False
    
    def check_and_update_price(self):
        """Main function to check prices and update if needed"""
        logger.info("Checking market prices...")
        
        # Ensure we're logged in
        if not self.funpay_account:
            if not self.login_to_funpay():
                logger.error("Failed to log in to FunPay, aborting price check")
                return
        
        # Get all sellers and prices
        sellers = self.get_market_prices()
        if not sellers:
            logger.warning("No sellers found or failed to get prices")
            return
        
        # Find my listing
        my_listing = self.find_my_listing(sellers)
        if not my_listing:
            logger.warning(f"My listing (lot ID: {self.config['lot_id']}) not found")
            return
        
        logger.info(f"My current price: {my_listing['price']}₽")
        
        # Find cheapest competitor
        cheapest_competitor = self.find_cheapest_competitor(sellers)
        if not cheapest_competitor:
            logger.info("No competitors found (all sellers are in whitelist)")
            return
        
        logger.info(f"Cheapest competitor: {cheapest_competitor['seller_name']} with price {cheapest_competitor['price']}₽")
        
        # Check if my price is already the cheapest
        if my_listing['price'] <= cheapest_competitor['price']:
            logger.info("My price is already the cheapest, no action needed")
            return
        
        # Calculate new price
        new_price = cheapest_competitor['price'] - self.config["price_decrease_amount"]
        
        # Check minimum price
        if new_price < self.config["min_price"]:
            logger.warning(f"New price ({new_price}₽) would be below minimum price ({self.config['min_price']}₽)")
            new_price = self.config["min_price"]
            
        # Update price
        if self.update_my_price(new_price):
            # Send notification
            notification_message = (
                f"✅ Цена успешно понижена!\n"
                f"Старая цена: {my_listing['price']}₽\n"
                f"Новая цена: {new_price}₽\n"
                f"Кого задампил: {cheapest_competitor['seller_name']} ({cheapest_competitor['price']}₽)"
            )
            self.send_telegram_notification(notification_message)

def main():
    dumper = PriceDumper(CONFIG)
    
    # Load configuration
    if not dumper.load_config_file():
        logger.error("Failed to load configuration, exiting")
        return
    
    # Login to FunPay
    if not dumper.login_to_funpay():
        logger.error("Failed to login to FunPay, exiting")
        return
    
    # Schedule the price checking task
    schedule.every(CONFIG["check_interval_minutes"]).minutes.do(dumper.check_and_update_price)
    
    logger.info(f"Auto-dumper started. Checking prices every {CONFIG['check_interval_minutes']} minutes")
    dumper.send_telegram_notification("🚀 Авто-дампер запущен и мониторит цены!")
    
    # Initial check
    dumper.check_and_update_price()
    
    # Main loop
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    main() 