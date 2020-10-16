from django.core.management.base import BaseCommand, CommandError
from stregsystem.models import MobilePayment, Member
from datetime import datetime, timedelta, timezone
from django.utils.dateparse import parse_datetime
import requests
from pathlib import Path
import json

class Command(BaseCommand):
  help = 'Imports the latests payments from MobilePay'

  api_endpoint = 'https://api.mobilepay.dk'
  # Saves secret tokens to the file "tokens.json" right next to this file.
  # Important to use a seperate file since the tokens can change and is thus not suitable for django settings.
  tokens_file = (Path(__file__).parent / 'tokens.json').as_posix()
  tokens = None

  #Reads the token file from disk
  def read_token_storage(self):
    with open(self.tokens_file) as json_file:
      self.tokens = json.load(json_file)

  #Saves the token variable to disk
  def update_token_storage(self):
    with open(self.tokens_file, 'w') as json_file:
      json.dump(self.tokens, json_file, indent=2)

  #Fetches a new access token using the refresh token.
  def refresh_access_token(self):
    url = f"{self.api_endpoint}/merchant-authentication-openidconnect/connect/token"

    payload = {
      "grant_type": "refresh_token",
      "refresh_token": self.tokens['refresh_token'],
      "client_id": self.tokens['zip-client-id'],
      "client_secret": self.tokens['zip-client-secret']
    }
    response = requests.post(url, data=payload)
    response.raise_for_status()
    json_response = response.json()
    # Calculate when the token expires
    expiretime = datetime.now() + timedelta(seconds=json_response['expires_in'] - 1)
    self.tokens['access_token_timeout'] = expiretime.isoformat(timespec='milliseconds')
    self.tokens['access_token'] = json_response['access_token']
    self.update_token_storage()

  def print_auth(self):
    #TODO: Do something
    pass

  # Format to timestamp format. Source:
  # https://github.com/MobilePayDev/MobilePay-TransactionReporting-API/blob/master/docs/api/types.md#timestamp
  def formatdatetime(self, inputdatetime):
    return f"{inputdatetime.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]}Z"

  #Fetches the transactions for a given paymentpoint (MobilePay phonenumber) in a given period (from-to)
  def get_transactions(self):
    url = f"{self.api_endpoint}/transaction-reporting/api/merchant/v1/paymentpoints/{self.tokens['paymentpoint']}/transactions"
    currenttime = datetime.now(timezone.utc)
    params = {
      'from': self.formatdatetime(currenttime - timedelta(days=7)),
      'to': self.formatdatetime(currenttime)
    }
    headers = {
      'x-ibm-client-secret': self.tokens['ibm-client-secret'],
      'x-ibm-client-id': self.tokens['ibm-client-id'],
      'authorization': 'Bearer {}'.format(self.tokens['access_token']) 
    }
    response = requests.get(url, params=params, headers=headers)
    response.raise_for_status()
    return response.json()['transactions']

  #Client side check if the token has expired.
  def refresh_expired_token(self):
    self.read_token_storage()
    expiretime = datetime.fromisoformat(self.tokens['access_token_timeout'])
    if datetime.now() >= expiretime:
      self.refresh_access_token()

  def fetch_transactions(self):
    #Do a client side check if token is good. If not - fetch another token.
    try:
      self.refresh_expired_token()
      return self.get_transactions()
    except:
      self.print_auth()
      pass

  def import_mobilepay_payments(self):
    transactions = self.fetch_transactions()
    if transactions is None:
      return

    for transaction in transactions:

      if transaction['type'] != 'Payment':
        continue

      trans_id = transaction['paymentTransactionId']

      if MobilePayment.objects.filter(transaction_id=trans_id).exists():
        self.write_notice(f'Skipping since it already exists (Transaction ID: {trans_id}')
        continue

      currencyCode = transaction['currencyCode']
      if currencyCode != 'DKK':
        self.write_warning(f'Does ONLY support DKK (Transaction ID: {trans_id}), was {currencyCode}')
        continue

      amount = transaction['amount']
      if amount < 50:
        self.write_warning(f'Only importing more than 50 DKK (Transaction ID: {trans_id}), was {amount}')
        continue

      amount *= 100
      comment = transaction['senderComment']
      strippedcomment = comment.strip()
      guessed_fember = None
      if Member.objects.filter(username=strippedcomment).exists():
        guessed_fember = Member.objects.get(username=strippedcomment)

      payment_datetime = parse_datetime(transaction['timestamp'])
      
      MobilePayment.objects.create(
        amount=amount,
        member=guessed_fember,
        comment=comment,
        timestamp=payment_datetime,
        transaction_id=trans_id,
        status=MobilePayment.UNSET
      )

    self.write_success('Successfully imported MobilePay payments!')


  def handle(self, *args, **options):
    self.import_mobilepay_payments()

  def write_warning(self, str):
    self.stdout.write(self.style.WARNING(str))

  def write_notice(self, str):
    self.stdout.write(self.style.NOTICE(str))

  def write_success(self, str):
    self.stdout.write(self.style.SUCCESS(str))
