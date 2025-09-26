
from azure.core.credentials import AzureKeyCredential
from azure.ai.documentintelligence import DocumentIntelligenceClient
from dotenv import load_dotenv
import os
import argparse
import json
from pathlib import Path

load_dotenv()
endpoint = os.getenv("AZURE_DOC_AI_ENDPOINT")
key = os.getenv("AZURE_DOC_AI_KEY")

filepath = '/Users/aaditya/Documents/Projects/Reducto_steamlit/testing_files/sample_multiple.pdf'
pages = 20,21

document_intelligence_client  = DocumentIntelligenceClient(
    endpoint=endpoint, credential=AzureKeyCredential(key)
)




with open(filepath, "rb") as f:
    poller = document_intelligence_client.begin_analyze_document(
        "prebuilt-tax.us.1040Schedule1", body=f, pages="20"
    )

tax1040 = poller.result()

doc = tax1040.documents[0]

def as_python(field):
    # Handle composites first
    if getattr(field, "value_object", None):
        return {k: as_python(v) for k, v in field.value_object.items()}
    if getattr(field, "value_array", None):
        return [as_python(v) for v in field.value_array]

    # Then try typed scalar values
    for attr in (
        "value_string","value_currency","value_integer","value_number",
        "value_boolean","value_date","value_time","value_phone_number",
        "value_address","value_selection_mark","value_country_region",
        "value_signature","value_url","value_email"  # some may be None
    ):
        v = getattr(field, attr, None)
        if v is not None:
            return v

    # Fallback to raw text
    return getattr(field, "content", None)

kv = {k: as_python(v) for k, v in doc.fields.items()}

print(kv)