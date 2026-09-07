"""Channel response — shapes the composed answer for the target channel.

Shaping only: chunk size, citation style and source handling differ per channel
(voice / web / whatsapp / sms), but the same ``Answer`` goes in every time and the
composer never learns which channel it served. The DSS shapes; it does not
deliver (design v2 §6.10).
"""
