import pyvisa
import sys
import platform
import subprocess
def diagnose_pyvisa():
    print("=== PyVISA Diagnostic Tool ===")
    print(f"Python version: {sys.version}")
    print(f"Platform: {platform.system()} {platform.release()}")
    
    try:
        print(f"PyVISA version: {pyvisa.__version__}")
    except:
        print("ERROR: PyVISA not installed!")
        return
    
    try:
        rm = pyvisa.ResourceManager()
        print(f"VISA backend: {rm.visalib}")
        
        resources = rm.list_resources()
        print(f"Found {len(resources)} instruments:")
        for resource in resources:
            print(f"  - {resource}")
            
        if not resources:
            print("NO INSTRUMENTS FOUND - See troubleshooting below")
        else:
            print("Instruments detected successfully")
            
    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
        print("This indicates a driver or installation problem")
# Run diagnosis
diagnose_pyvisa()
