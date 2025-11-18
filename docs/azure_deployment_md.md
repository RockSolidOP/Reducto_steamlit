# Azure Deployment Guide – Containerized App

This guide explains how to build, tag, push, and deploy a container image (`<IMAGE_NAME>`) using Azure Container Registry (ACR) and Azure App Service. It also includes Rancher Desktop prerequisites for Windows, TR M‑account requirements, and future infra considerations.

---

## 1. Overview
A complete workflow for:
- Building Docker images
- Logging into Azure & ACR
- Tagging & pushing images
- Deploying containers to Azure App Service
- Validating deployment
- Setting up Rancher Desktop (Windows)

---

## 2. Prerequisites

### 2.1 Rancher Desktop (Windows)
**Install Rancher Desktop:**  
https://trten.sharepoint.com/sites/intr-docker/SitePages/Install-Rancher-Desktop(-Step-by-Step.aspx

**If WSL2 is not installed:**  
https://learn.microsoft.com/windows/wsl/install

#### Notes for Rancher Desktop on Windows
- Set runtime to **dockerd (moby)**:  
  *Rancher Desktop → Settings → Container Engine*
- Disable Kubernetes unless required
- Ensure WSL2 backend is enabled:  
  ```bash
  wsl --set-default-version 2
  ```

#### Troubleshooting (Rancher Desktop + WSL2)
- **docker: command not found / cannot connect**  
  Ensure runtime is dockerd and restart terminal
- **Images missing after runtime switch**  
  dockerd & containerd have separate stores
- **WSL error 0x80370102**  
  Enable virtualization + WSL components, then reboot
- **Proxy/network issues**  
  Configure proxy in Rancher Desktop (Settings → Network)

---

## 2.2 General Requirements
- Docker CLI (via Rancher Desktop)
- Azure CLI installed
- Access to **ConversionsStreamlitapp** ACR
- TR M‑account with permissions:
  - AcrPull
  - App Service Contributor
  - Storage Blob Data Contributor

---

## 3. Build the Docker Image
### Latest build
```bash
docker build -t <IMAGE_NAME>:latest .
```

### Versioned build (PowerShell)
```powershell
$tag = (Get-Date -Format 'yyyy.MM.dd-HHmm')
docker build -t <IMAGE_NAME>:latest -t <IMAGE_NAME>:$tag .
```

---

## 4. Authenticate With Azure & ACR
### Login to Azure
```bash
az login --use-device-code
az account show -o table
```

### Login to ACR
```bash
az acr login -n conversionsstreamlitapp
```

If login fails:
```powershell
$usr = az acr credential show -n conversionsstreamlitapp --query username -o tsv
$pwd = az acr credential show -n conversionsstreamlitapp --query passwords[0].value -o tsv
docker login conversionsstreamlitapp.azurecr.io -u $usr -p $pwd
```

---

## 5. Tag the Docker Image
### Latest
```bash
docker tag <IMAGE_NAME>:latest conversionsstreamlitapp.azurecr.io/<IMAGE_NAME>:latest
```

### Versioned tag
```powershell
$tag = (Get-Date -Format 'yyyy.MM.dd-HHmm')
docker tag <IMAGE_NAME>:latest conversionsstreamlitapp.azurecr.io/<IMAGE_NAME>:$tag
```

---

## 6. Push the Image to ACR
```bash
docker push conversionsstreamlitapp.azurecr.io/<IMAGE_NAME>:latest
docker push conversionsstreamlitapp.azurecr.io/<IMAGE_NAME>:$tag
```

---

## 7. Verify in ACR
```bash
az acr repository list -n conversionsstreamlitapp -o table
az acr repository show-tags -n conversionsstreamlitapp --repository <IMAGE_NAME> -o table
```

---

## 8. Configure Azure App Service (Container Settings)
In Azure Portal:
1. Go to **App Services**
2. Select your resource (TR M‑tenant)
3. Open **Deployment → Container Settings**
4. Fill in settings:
   - **Image source:** Azure Container Registry
   - **Subscription:** DCO-ATPConversionPrograms-N
   - **Registry:** ConversionsStreamlitapp
   - **Authentication:** Admin Credentials
   - **Image:** `<IMAGE_NAME>`
   - **Tag:** Select pushed image tag

*(Screenshot example available in DOCX version)*

---

## 9. Restart and Validate
- Restart the App Service (Overview → Restart)
- Check logs under **Monitoring → Container Logs**
- Validate application loads in browser

---

## 10. TR M‑Account Access Notes
Future work may store:
- Model versions
- Embedding datasets
- PDF samples and large extraction outputs

TR M‑account must have access to:
- Azure App Service
- Azure Storage (Blob)
- Azure Container Registry

---

## 11. Future Infra Notes
- Embeddings + model files will be stored in Azure Storage
- Daily/weekly model versioning may be required
- Ensure Blob Storage access early to avoid deployment failures

---

