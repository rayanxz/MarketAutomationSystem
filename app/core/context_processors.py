def global_fx_context(request):
    """
    Expose the current global FX (SYP per USD) for topbar display.
    Uses the same source as billing pages: FinSV.get_current_fx_syp_per_usd().
    """
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}

    from financials import services as FinSV

    try:
        fx = FinSV.get_current_fx_syp_per_usd()
    except Exception:
        fx = None

    return {"global_fx_syp_per_usd": fx}
