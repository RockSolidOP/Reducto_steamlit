import os
import json
import base64
import fitz
from dotenv import load_dotenv
from azure.core.credentials import AzureKeyCredential
from azure.ai.documentintelligence import DocumentIntelligenceClient
import re

saved_azure_output_path = "testing_files/azure_docai_ustax/saved_azure_results"
selected_fields = None


def extract_with_azure(pdf_filepath):
    # ---- Check for Azure Credentials
    load_dotenv()

    # Support both naming conventions used across the repo/README
    endpoint = (
        os.getenv("AZURE_DOC_AI_ENDPOINT")
        or os.getenv("AZURE_ENDPOINT")
    )
    key = (
        os.getenv("AZURE_DOC_AI_KEY")
        or os.getenv("AZURE_KEY")
    )

    if not endpoint or not key:
        return {
            "result": {"message": "Azure credentials are missing."}
        }
    
    # ---- Check for the directory to store results
    results_dir = saved_azure_output_path
    os.makedirs(results_dir, exist_ok=True)

    base_name = os.path.splitext(os.path.basename(pdf_filepath))[0]
    json_filepath = os.path.join(results_dir, f"{base_name}.json")

    if not os.path.exists(json_filepath):
        try:
            document_intelligence_client = DocumentIntelligenceClient(endpoint=endpoint, credential=AzureKeyCredential(key))
            with open(pdf_filepath, "rb") as f:
                extractor = document_intelligence_client.begin_analyze_document("prebuilt-tax.us", body=f)
            ustaxes = extractor.result()
            if not ustaxes.documents:
                return {
                    "result": {"message": "No Documents found in analysis result."}
                }
            output_docs = [doc.as_dict() for doc in ustaxes.documents]

            with open(json_filepath, "w") as jf:
                json.dump(output_docs, jf, indent=4)
            print(f"Results saved to {json_filepath}")
        except Exception as e:
            return {
                "result": {"message": f"Error Accessing Azure: {str(e)}"}
            }
    
    try:
        with open(json_filepath, "r") as jf:
            document_list = json.load(jf)
    except FileNotFoundError:
        print(f"Error: The file '{json_filepath}' was not found.")
        return
    except json.JSONDecodeError:
        print(f"Error: Could not decode JSON from '{json_filepath}'. Check the file format.")
        return
    
    if not isinstance(document_list, list):
        print(f"Error: The top level element in the json file is not a list.")
        return
    
    # ---- Iterate through doucments
    for i, doc in enumerate(document_list):
        print(f"\n------------ PROCESSING DOCUMENT {i+1}: {doc.get('docType', 'N/A')}")
        if selected_fields:
            filtered_fields = {k: v for k, v in doc.get("fields", {}).items() if k in selected_fields}
            doc["fields"] = filtered_fields
        ret_data = doc["fields"]
        return {
            "result": ret_data
        }

if __name__ == "__main__":
    dct = extract_with_azure("testing_files/azure_docai_ustax/data/002_1040_2024.pdf")
    print(dct["result"])
