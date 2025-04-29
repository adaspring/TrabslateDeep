name: HTML Translation Pipeline

on:
  workflow_dispatch:
    inputs:
      source_file:
        description: 'Source HTML file path'
        required: true
      target_lang:
        description: 'Target language code (e.g., fr)'
        required: true
      output_file:
        description: 'Output HTML file path'
        required: true

env:
  PYTHON_VERSION: '3.10'
  DEEPL_KEY: ${{ secrets.DEEPL_KEY }}
  LIBRE_URLS: ${{ secrets.LIBRE_URLS }}
  CHATGPT_KEY: ${{ secrets.CHATGPT_KEY }}

jobs:
  translate:
    runs-on: ubuntu-latest
    steps:
    - name: Checkout repository
      uses: actions/checkout@v4

    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: ${{ env.PYTHON_VERSION }}

    - name: Install dependencies
      run: |
        python -m pip install --upgrade pip
        pip install -r requirements.txt
        pip install requests beautifulsoup4

    - name: Configure Libre servers
      run: |
        if [ -n "$LIBRE_URLS" ]; then
          echo "Using custom Libre servers: $LIBRE_URLS"
        else
          echo "Using default Libre servers"
        fi

    - name: Run translation process
      run: |
        python translator.py \
          "${{ inputs.source_file }}" \
          "${{ inputs.output_file }}" \
          "${{ inputs.target_lang }}"
      env:
        DEEPL_KEY: ${{ env.DEEPL_KEY }}
        LIBRE_URLS: ${{ env.LIBRE_URLS }}
        CHATGPT_KEY: ${{ env.CHATGPT_KEY }}

    - name: Upload artifact
      uses: actions/upload-artifact@v3
      with:
        name: translated-file
        path: ${{ inputs.output_file }}

    - name: Cleanup workspace
      run: |
        rm -f ${{ inputs.output_file }}
