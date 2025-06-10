import asyncio
import json
import logging
import os
import smtplib
from datetime import datetime, time
from typing import Dict, List, Optional
from dataclasses import dataclass
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import yfinance as yf
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")


@dataclass
class StockConfig:
    symbol: str
    company_name: str
    quantity_factor: float
    loan_outstanding: float
    security_cover_threshold: float


@dataclass
class SenderConfig:
    from_email: str
    from_name: str
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    use_tls: bool = True


@dataclass
class RecipientConfig:
    email: str
    subscribed_symbols: List[str]
    name: str = ""
    cc: List[str] = None
    bcc: List[str] = None
    alert_preferences: Dict[str, bool] = None
    
    def __post_init__(self):
        if self.cc is None:
            self.cc = []
        if self.bcc is None:
            self.bcc = []
        if self.alert_preferences is None:
            self.alert_preferences = {
                "scheduled_reports": True,
                "threat_alerts": True,
                "manual_alerts": True
            }


class ConfigManager:
    def __init__(self, config_path: str = "config.json"):
        self.config_path = Path(config_path)
        self.config = self._load_config()
    
    def _load_config(self) -> Dict:
        try:
            with open(self.config_path, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            self._create_default_config()
            return self._load_config()
    
    def _create_default_config(self):
        default_config = {
            "currency_symbol": "₹",
            "stocks": [
                {
                    "symbol": "STAR.NS",
                    "company_name": "Strides Pharma Science Limited",
                    "quantity_factor": 1150000,
                    "loan_outstanding": 4800000,
                    "security_cover_threshold": 1.7
                }
            ],
            "email_sender": {
                "from_email": "noreply@yourdomain.com",
                "from_name": "Stock Watchdog",
                "smtp_host": "smtp.hostinger.com",
                "smtp_port": 587,
                "smtp_username": "noreply@yourdomain.com",
                "smtp_password": "",
                "use_tls": True
            },
            "recipients": [
                {
                    "email": "trader@yourcompany.com",
                    "subscribed_symbols": ["STAR.NS"]
                }
            ],
            "schedule": {
                "daily_reports": ["09:30", "12:30", "16:00"],
                "timezone": "US/Eastern"
            }
        }
        with open(self.config_path, 'w') as f:
            json.dump(default_config, f, indent=4)
    
    def get_stocks(self) -> List[StockConfig]:
        return [StockConfig(**stock) for stock in self.config.get("stocks", [])]
    
    def get_sender_config(self) -> SenderConfig:
        sender_data = self.config["email_sender"].copy()
        
        if 'smtp_password' not in sender_data or not sender_data['smtp_password']:
            env_password = os.getenv('SMTP_PASSWORD')
            if env_password:
                sender_data['smtp_password'] = env_password
        
        return SenderConfig(**sender_data)

    def get_recipients(self) -> List[RecipientConfig]:
        return [RecipientConfig(**recipient) for recipient in self.config.get("recipients", [])]
    
    def get_schedule_times(self) -> List[str]:
        return self.config["schedule"]["daily_reports"]

    def get_timezone(self) -> str:
        return self.config["schedule"]["timezone"]

    def get_currency_symbol(self) -> str:
        return self.config.get("currency_symbol", "₹")


class StockPriceMonitor:
    def __init__(self):
        self.cache = {}
        self.last_update = {}
    
    async def get_stock_data(self, stock_config: StockConfig) -> Dict:
        symbol = stock_config.symbol
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info
            
            # Get 5-day history to ensure we have yesterday's data
            hist_5d = ticker.history(period="5d", interval="1d")
            hist_1d = ticker.history(period="1d", interval="1m")
            
            # Current price logic
            if hist_1d.empty:
                current_price = info.get('currentPrice') or info.get('previousClose')
                if not current_price:
                    raise ValueError(f"No data available for {symbol}")
                current_volume = info.get('volume', 0)
            else:
                current_price = hist_1d['Close'].iloc[-1]
                current_volume = int(hist_1d['Volume'].iloc[-1])

            # Yesterday's closing price and previous close
            prev_close = info.get('previousClose', current_price)
            yesterday_close = prev_close  # Default fallback
            
            if not hist_5d.empty and len(hist_5d) >= 2:
                yesterday_close = hist_5d['Close'].iloc[-2]  # Yesterday's close
                prev_close = hist_5d['Close'].iloc[-1]  # Last trading day close
            
            # Current day change (vs previous close)
            current_change = current_price - prev_close
            current_change_percent = (current_change / prev_close) * 100 if prev_close else 0
            
            # Yesterday's change (vs day before yesterday)
            yesterday_change = 0.0
            yesterday_change_percent = 0.0
            if not hist_5d.empty and len(hist_5d) >= 3:
                day_before_yesterday = hist_5d['Close'].iloc[-3]
                yesterday_change = yesterday_close - day_before_yesterday
                yesterday_change_percent = (yesterday_change / day_before_yesterday) * 100 if day_before_yesterday else 0

            security_cover = 0.0
            if stock_config.loan_outstanding > 0:
                security_cover = (stock_config.quantity_factor * current_price) / stock_config.loan_outstanding

            data = {
                "symbol": symbol,
                "current_price": round(current_price, 2),
                "previous_close": round(prev_close, 2),
                "yesterday_close": round(yesterday_close, 2),
                "change": round(current_change, 2),
                "change_percent": round(current_change_percent, 2),
                "yesterday_change": round(yesterday_change, 2),
                "yesterday_change_percent": round(yesterday_change_percent, 2),
                "volume": current_volume,
                "timestamp": datetime.now().isoformat(),
                "market_cap": info.get('marketCap'),
                "pe_ratio": info.get('trailingPE'),
                "52_week_high": info.get('fiftyTwoWeekHigh'),
                "52_week_low": info.get('fiftyTwoWeekLow'),
                "security_cover": round(security_cover, 2)
            }
            
            self.cache[symbol] = data
            self.last_update[symbol] = datetime.now()
            return data
            
        except Exception as e:
            logging.error(f"Error fetching data for {symbol}: {e}")
            if symbol in self.cache:
                logging.warning(f"Returning cached data for {symbol} due to error.")
                return self.cache[symbol]
            raise
    
    async def check_multiple_stocks(self, stock_configs: List[StockConfig]) -> Dict[str, Dict]:
        tasks = [self.get_stock_data(sc) for sc in stock_configs]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        stock_data_map = {}
        for stock_config_item, result in zip(stock_configs, results):
            symbol = stock_config_item.symbol
            if isinstance(result, Exception):
                logging.error(f"Failed to fetch {symbol}: {result}")
                if symbol in self.cache:
                    logging.warning(f"Using cached data for {symbol} in multi-check due to error.")
                    stock_data_map[symbol] = self.cache[symbol]
                continue
            stock_data_map[symbol] = result
        
        return stock_data_map


class EmailService:
    def __init__(self, config: SenderConfig):
        self.config = config
        
        if not self.config.smtp_password:
            logging.warning("SMTP password not configured. Please set it in config.json or SMTP_PASSWORD environment variable.")
    
    async def send_alert(self, recipient_config: RecipientConfig, subject: str, content: str):
        if not self.config.smtp_password:
            logging.error("Cannot send email: SMTP password not configured.")
            return
        
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = f"{self.config.from_name} <{self.config.from_email}>"
        msg['To'] = recipient_config.email
        
        # Add CC recipients
        if recipient_config.cc:
            msg['Cc'] = ', '.join(recipient_config.cc)
        
        # Note: BCC recipients are not added to headers, but included in send_message
        
        html_part = MIMEText(content, 'html')
        msg.attach(html_part)
        
        try:
            await asyncio.to_thread(self._send_smtp_email, msg, recipient_config)
            recipient_name = recipient_config.name or recipient_config.email
            logging.info(f"Email sent successfully to {recipient_name}")
        except Exception as e:
            logging.error(f"Failed to send email to {recipient_config.email}: {e}")
    
    def _send_smtp_email(self, msg: MIMEMultipart, recipient_config: RecipientConfig):
        # Build the complete recipient list (TO + CC + BCC)
        all_recipients = [recipient_config.email]
        all_recipients.extend(recipient_config.cc)
        all_recipients.extend(recipient_config.bcc)
        
        with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port) as server:
            if self.config.use_tls:
                server.starttls()
            server.login(self.config.smtp_username, self.config.smtp_password)
            server.send_message(msg, to_addrs=all_recipients)
    
    def format_grouped_alert(self, all_stock_data: List[Dict], all_stock_configs: List[StockConfig], currency_symbol: str) -> str:
        email_body_html = ""
        
        for stock_data in all_stock_data:
            symbol = stock_data.get("symbol")
            stock_config = next((sc for sc in all_stock_configs if sc.symbol == symbol), None)
            if not stock_config:
                continue

            is_cover_breached = stock_data.get("security_cover", 999) < stock_config.security_cover_threshold

            header_text = f"Update for {stock_config.company_name} ({symbol})"
            header_style = "color: blue;"
            if is_cover_breached:
                header_text = f"ATTENTION: {stock_config.company_name} ({symbol})"
                header_style = "color: red;"
            
            cover_style = "color: red; font-weight: bold;" if is_cover_breached else ""
            
            current_price_val = stock_data.get("current_price", 0.0)
            previous_close_val = stock_data.get("previous_close", 0.0)
            yesterday_close_val = stock_data.get("yesterday_close", 0.0)
            current_change_val = stock_data.get("change", 0.0)
            current_change_percent_val = stock_data.get("change_percent", 0.0)
            yesterday_change_val = stock_data.get("yesterday_change", 0.0)
            yesterday_change_percent_val = stock_data.get("yesterday_change_percent", 0.0)
            security_cover_val = stock_data.get("security_cover", 0.0)
            
            # Color coding for price changes
            current_change_color = "green" if current_change_val >= 0 else "red"
            yesterday_change_color = "green" if yesterday_change_val >= 0 else "red"
            current_change_sign = "+" if current_change_val >= 0 else ""
            yesterday_change_sign = "+" if yesterday_change_val >= 0 else ""

            email_body_html += f"""
            <h3 style="{header_style}">{header_text}</h3>
            <table style="border-collapse: collapse; width: 100%; font-family: Arial, sans-serif; margin-bottom: 20px;">
                <tr style="background-color: #f2f2f2;"><td colspan="2" style="border: 1px solid #dddddd; text-align: center; padding: 8px; font-weight: bold; font-size: 14px;">📊 Current Trading Data</td></tr>
                <tr><td style="border: 1px solid #dddddd; text-align: left; padding: 8px; font-weight: bold;">Current Market Price (CMP)</td><td style="border: 1px solid #dddddd; text-align: left; padding: 8px; font-weight: bold; font-size: 16px;">{currency_symbol}{current_price_val:,.2f}</td></tr>
                <tr><td style="border: 1px solid #dddddd; text-align: left; padding: 8px; font-weight: bold;">Previous Close</td><td style="border: 1px solid #dddddd; text-align: left; padding: 8px;">{currency_symbol}{previous_close_val:,.2f}</td></tr>
                <tr><td style="border: 1px solid #dddddd; text-align: left; padding: 8px; font-weight: bold;">Current Day Change</td><td style="border: 1px solid #dddddd; text-align: left; padding: 8px; color: {current_change_color}; font-weight: bold;">{current_change_sign}{currency_symbol}{current_change_val:,.2f} ({current_change_sign}{current_change_percent_val:.2f}%)</td></tr>
                
                <tr style="background-color: #f2f2f2;"><td colspan="2" style="border: 1px solid #dddddd; text-align: center; padding: 8px; font-weight: bold; font-size: 14px;">📈 Yesterday's Performance</td></tr>
                <tr><td style="border: 1px solid #dddddd; text-align: left; padding: 8px; font-weight: bold;">Yesterday's Close</td><td style="border: 1px solid #dddddd; text-align: left; padding: 8px;">{currency_symbol}{yesterday_close_val:,.2f}</td></tr>
                <tr><td style="border: 1px solid #dddddd; text-align: left; padding: 8px; font-weight: bold;">Yesterday's Change</td><td style="border: 1px solid #dddddd; text-align: left; padding: 8px; color: {yesterday_change_color}; font-weight: bold;">{yesterday_change_sign}{currency_symbol}{yesterday_change_val:,.2f} ({yesterday_change_sign}{yesterday_change_percent_val:.2f}%)</td></tr>
                
                <tr style="background-color: #f2f2f2;"><td colspan="2" style="border: 1px solid #dddddd; text-align: center; padding: 8px; font-weight: bold; font-size: 14px;">🔒 Security Cover Analysis</td></tr>
                <tr><td style="border: 1px solid #dddddd; text-align: left; padding: 8px; font-weight: bold;">Required Security Cover</td><td style="border: 1px solid #dddddd; text-align: left; padding: 8px;">{stock_config.security_cover_threshold:.2f}x</td></tr>
                <tr><td style="border: 1px solid #dddddd; text-align: left; padding: 8px; font-weight: bold;">Current Security Cover</td><td style="border: 1px solid #dddddd; text-align: left; padding: 8px; {cover_style}">{security_cover_val:.2f}x</td></tr>
            </table>
            """

        return f"""
        <html>
            <head>
                <style>
                    body {{ font-family: Arial, sans-serif; margin: 20px; }}
                    table, th, td {{ border: 1px solid #dddddd; }}
                    td, th {{ text-align: left; padding: 8px; }}
                    .header {{ background-color: #f2f2f2; font-weight: bold; }}
                </style>
            </head>
            <body>
                <h2 style="color: #333;">📊 Stock Watchdog Report</h2>
                <p style="color: #666; font-size: 14px;">Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}</p>
                {email_body_html}
                <hr style="margin-top: 20px;">
                <p style="font-size: 12px; color: #888;"><em>This is an automated report generated by Stock Watchdog</em></p>
            </body>
        </html>
        """


class StockWatchdog:
    def __init__(self):
        self.config_manager = ConfigManager()
        self.monitor = StockPriceMonitor()
        self.email_service = EmailService(self.config_manager.get_sender_config())
        self.scheduler = AsyncIOScheduler()
        self.app = FastAPI(title="Stock Price Watchdog")
        self._setup_routes()
        self._setup_scheduler()
    
    def _setup_routes(self):
        @self.app.get("/")
        async def root():
            return {"message": "Stock Price Watchdog API", "status": "running"}
        
        @self.app.get("/stock/{symbol}")
        async def get_stock_price(symbol: str):
            upper_symbol = symbol.upper()
            all_stock_configs = self.config_manager.get_stocks()
            stock_config_obj = next((s for s in all_stock_configs if s.symbol.upper() == upper_symbol), None)

            if not stock_config_obj:
                raise HTTPException(status_code=404, detail=f"Stock {upper_symbol} not found in configuration.")
            try:
                data = await self.monitor.get_stock_data(stock_config_obj)
                return JSONResponse(content=data)
            except Exception as e:
                logging.error(f"Error in /stock/{upper_symbol} endpoint: {e}", exc_info=True)
                raise HTTPException(status_code=500, detail=f"Error fetching data for {upper_symbol}: {str(e)}")
        
        @self.app.get("/stocks")
        async def get_all_monitored_stocks():
            stocks_config_list = self.config_manager.get_stocks()
            if not stocks_config_list:
                return JSONResponse(content={})
            try:
                data = await self.monitor.check_multiple_stocks(stocks_config_list)
                return JSONResponse(content=data)
            except Exception as e:
                logging.error(f"Error in /stocks endpoint: {e}", exc_info=True)
                raise HTTPException(status_code=500, detail=f"Error fetching data for all stocks: {str(e)}")
        
        @self.app.post("/alert/{symbol}")
        async def trigger_manual_alert(symbol: str):
            upper_symbol = symbol.upper()
            all_stocks_config = self.config_manager.get_stocks()
            stock_config_obj = next((s for s in all_stocks_config if s.symbol.upper() == upper_symbol), None)

            if not stock_config_obj:
                raise HTTPException(status_code=404, detail=f"Stock {upper_symbol} not in watchlist.")
            
            try:
                stock_data = await self.monitor.get_stock_data(stock_config_obj)
                currency_symbol = self.config_manager.get_currency_symbol()
                
                recipients_to_notify = [
                    r for r in self.config_manager.get_recipients() 
                    if upper_symbol in r.subscribed_symbols and r.alert_preferences.get("manual_alerts", True)
                ]
                if not recipients_to_notify:
                    return {"message": f"Alert for {upper_symbol} processed, but no recipients are subscribed or have manual alerts enabled."}

                is_cover_breached = stock_data.get("security_cover", 999) < stock_config_obj.security_cover_threshold
                subject = "BOT ALERT!" if is_cover_breached else "Manual Stock Update"
                final_subject = f"{subject}: {stock_config_obj.symbol}"
                
                content = self.email_service.format_grouped_alert([stock_data], [stock_config_obj], currency_symbol)

                for recipient in recipients_to_notify:
                    await self.email_service.send_alert(recipient, final_subject, content)
                
                return {"message": f"Alert for {upper_symbol} sent to {len(recipients_to_notify)} recipient(s)."}
            except Exception as e:
                logging.error(f"Error in /alert/{upper_symbol} endpoint: {e}", exc_info=True)
                raise HTTPException(status_code=500, detail=f"Error processing manual alert for {upper_symbol}: {str(e)}")
    
    def _setup_scheduler(self):
        schedule_times = self.config_manager.get_schedule_times()
        timezone = self.config_manager.get_timezone()
        
        for schedule_time in schedule_times:
            hour, minute = map(int, schedule_time.split(':'))
            self.scheduler.add_job(
                self._scheduled_check,
                CronTrigger(hour=hour, minute=minute, timezone=timezone),
                id=f"daily_check_{schedule_time}",
                replace_existing=True
            )
        
        self.scheduler.add_job(
            self._continuous_threat_check,
            'interval',
            minutes=5,
            id="threat_monitor",
            replace_existing=True
        )
    
    async def _scheduled_check(self):
        await self._check_all_stocks(is_scheduled=True)
    
    async def _continuous_threat_check(self):
        await self._check_all_stocks(is_scheduled=False, threat_only=True)
    
    async def _check_all_stocks(self, is_scheduled: bool = False, threat_only: bool = False):
        all_stocks_config = self.config_manager.get_stocks()
        recipients = self.config_manager.get_recipients()
        currency_symbol = self.config_manager.get_currency_symbol()

        if not all_stocks_config or not recipients:
            logging.info("_check_all_stocks: No stocks or recipients configured.")
            return

        try:
            fetched_stock_data_map = await self.monitor.check_multiple_stocks(all_stocks_config)
            
            # Process each recipient individually
            for recipient in recipients:
                # Check alert preferences
                alert_prefs = recipient.alert_preferences
                should_send_scheduled = is_scheduled and alert_prefs.get("scheduled_reports", True)
                should_send_threat = threat_only and alert_prefs.get("threat_alerts", True)
                
                if not (should_send_scheduled or should_send_threat):
                    continue
                
                # Get stock data for this recipient's subscribed symbols
                recipient_stock_data = []
                for symbol in recipient.subscribed_symbols:
                    if symbol in fetched_stock_data_map:
                        recipient_stock_data.append(fetched_stock_data_map[symbol])
                
                if not recipient_stock_data:
                    continue

                # Check if any security cover is breached
                is_any_cover_breached = False
                for stock_data in recipient_stock_data:
                    symbol = stock_data.get("symbol")
                    stock_config = next((sc for sc in all_stocks_config if sc.symbol == symbol), None)
                    if stock_config and stock_data.get("security_cover", 999) < stock_config.security_cover_threshold:
                        is_any_cover_breached = True
                        break
                
                # Determine if we should send email
                should_send_email_now = (should_send_scheduled and not threat_only) or (should_send_threat and is_any_cover_breached)

                if should_send_email_now:
                    subject = "BOT ALERT!" if is_any_cover_breached else "Stock Update"
                    if is_scheduled:
                        subject = f"Scheduled Report - {subject}"
                    
                    email_content = self.email_service.format_grouped_alert(
                        recipient_stock_data, all_stocks_config, currency_symbol
                    )
                    await self.email_service.send_alert(recipient, subject, email_content)
                    
                    recipient_name = recipient.name or recipient.email
                    cc_bcc_info = ""
                    if recipient.cc:
                        cc_bcc_info += f" (CC: {', '.join(recipient.cc)})"
                    if recipient.bcc:
                        cc_bcc_info += f" (BCC: {len(recipient.bcc)} recipients)"
                    
                    logging.info(f"Email sent to {recipient_name} with {len(recipient_stock_data)} stock(s). Alert: {is_any_cover_breached}{cc_bcc_info}")

        except Exception as e:
            logging.error(f"Error in _check_all_stocks: {e}", exc_info=True)
    
    async def start(self):
        self.scheduler.start()
        logging.info("Stock Watchdog started successfully")
    
    async def stop(self):
        self.scheduler.shutdown()
        logging.info("Stock Watchdog stopped")


def setup_logging():
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('logs/app.log'),
            logging.StreamHandler()
        ]
    )

async def main():
    setup_logging()
    logging.info("Starting Stock Watchdog service")
    
    watchdog = StockWatchdog()
    await watchdog.start()
    
    try:
        import uvicorn
        config = uvicorn.Config(
            app=watchdog.app,
            host="127.0.0.1",
            port=8000,
            log_level="info",
            access_log=True
        )
        server = uvicorn.Server(config)
        await server.serve()
    except KeyboardInterrupt:
        logging.info("Shutting down Stock Watchdog service")
        await watchdog.stop()
    except Exception as e:
        logging.error(f"Service error: {e}")
        await watchdog.stop()
        raise


if __name__ == "__main__":
    asyncio.run(main())