def check_license_or_raise():
    """
    TODO: In packaging phase:
      - Check registry key HKLM\Software\MarketPOS\InstallGUID
      - Check %ProgramData%\MarketPOS\license.dat signature
      - Validate machine binding.
    For now: do nothing (dev mode).
    """
    return True
