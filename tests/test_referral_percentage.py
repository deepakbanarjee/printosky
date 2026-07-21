import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.index import _credit_referrer, _acad_auth_staff

def test_credit_referrer_flat_amount():
    """Test that a flat positive credit amount is saved directly."""
    with patch("db_cloud._client") as mock_client:
        mock_db = MagicMock()
        mock_client.return_value = mock_db
        
        select_sessions = MagicMock()
        select_sessions.eq.return_value.execute.return_value.data = [{"referral_code": "FLATCODE"}]
        
        select_dup = MagicMock()
        select_dup.eq.return_value.eq.return_value.execute.return_value.data = []
        
        select_referrer = MagicMock()
        select_referrer.eq.return_value.execute.return_value.data = [{"credit_amount": 25}]
        
        ref_credits_table = MagicMock()
        insert_mock = MagicMock()
        ref_credits_table.insert.return_value = insert_mock
        ref_credits_table.select.return_value = select_dup
        
        def table_side_effect(tname):
            if tname == "bot_sessions":
                m = MagicMock()
                m.select.return_value = select_sessions
                return m
            elif tname == "referral_credits":
                return ref_credits_table
            elif tname == "referrers":
                m = MagicMock()
                m.select.return_value = select_referrer
                return m
            return MagicMock()
            
        mock_db.table.side_effect = table_side_effect
        
        _credit_referrer("919999999999", "J123")
        
        # Verify the insert payload
        ref_credits_table.insert.assert_called_once()
        payload = ref_credits_table.insert.call_args[0][0]
        
        assert payload["referrer_code"] == "FLATCODE"
        assert payload["amount_inr"] == 25
        insert_mock.execute.assert_called_once()


def test_credit_referrer_percentage_batch():
    """Test that a negative credit_amount uses batch total_amount to compute credit."""
    with patch("db_cloud._client") as mock_client, \
         patch("db_cloud.get_batch") as mock_get_batch, \
         patch("db_cloud.get_job") as mock_get_job:
         
        mock_db = MagicMock()
        mock_client.return_value = mock_db
        
        mock_get_batch.return_value = {"total_amount": 350.0}
        mock_get_job.return_value = {}
        
        select_sessions = MagicMock()
        select_sessions.eq.return_value.execute.return_value.data = [{"referral_code": "PERCENTCODE"}]
        
        select_dup = MagicMock()
        select_dup.eq.return_value.eq.return_value.execute.return_value.data = []
        
        # -10 means 10%
        select_referrer = MagicMock()
        select_referrer.eq.return_value.execute.return_value.data = [{"credit_amount": -10}]
        
        ref_credits_table = MagicMock()
        insert_mock = MagicMock()
        ref_credits_table.insert.return_value = insert_mock
        ref_credits_table.select.return_value = select_dup
        
        def table_side_effect(tname):
            if tname == "bot_sessions":
                m = MagicMock()
                m.select.return_value = select_sessions
                return m
            elif tname == "referral_credits":
                return ref_credits_table
            elif tname == "referrers":
                m = MagicMock()
                m.select.return_value = select_referrer
                return m
            return MagicMock()
            
        mock_db.table.side_effect = table_side_effect
        
        _credit_referrer("919999999999", "B123")
        
        ref_credits_table.insert.assert_called_once()
        payload = ref_credits_table.insert.call_args[0][0]
        
        assert payload["referrer_code"] == "PERCENTCODE"
        # 10% of 350 is 35
        assert payload["amount_inr"] == 35
        insert_mock.execute.assert_called_once()


def test_credit_referrer_percentage_single_job():
    """Test that a negative credit_amount uses single job amount to compute credit."""
    with patch("db_cloud._client") as mock_client, \
         patch("db_cloud.get_batch") as mock_get_batch, \
         patch("db_cloud.get_job") as mock_get_job:
         
        mock_db = MagicMock()
        mock_client.return_value = mock_db
        
        mock_get_batch.return_value = {}
        # Job total paid is 245.0
        mock_get_job.return_value = {"amount_collected": 245.0}
        
        select_sessions = MagicMock()
        select_sessions.eq.return_value.execute.return_value.data = [{"referral_code": "PERCENTCODE"}]
        
        select_dup = MagicMock()
        select_dup.eq.return_value.eq.return_value.execute.return_value.data = []
        
        # -10 means 10%
        select_referrer = MagicMock()
        select_referrer.eq.return_value.execute.return_value.data = [{"credit_amount": -10}]
        
        ref_credits_table = MagicMock()
        insert_mock = MagicMock()
        ref_credits_table.insert.return_value = insert_mock
        ref_credits_table.select.return_value = select_dup
        
        def table_side_effect(tname):
            if tname == "bot_sessions":
                m = MagicMock()
                m.select.return_value = select_sessions
                return m
            elif tname == "referral_credits":
                return ref_credits_table
            elif tname == "referrers":
                m = MagicMock()
                m.select.return_value = select_referrer
                return m
            return MagicMock()
            
        mock_db.table.side_effect = table_side_effect
        
        _credit_referrer("919999999999", "J123")
        
        ref_credits_table.insert.assert_called_once()
        payload = ref_credits_table.insert.call_args[0][0]
        
        assert payload["referrer_code"] == "PERCENTCODE"
        # 10% of 245 is 24.5, rounded to 24 by Python round() banker's rounding
        assert payload["amount_inr"] == 24
        insert_mock.execute.assert_called_once()


def test_acad_auth_staff_admin_bypass():
    """Test that a valid X-Admin-Password header bypasses the staff PIN check."""
    with patch("api.index._auth_admin_pw") as mock_auth:
        mock_auth.return_value = True
        
        # Create a mock request handler with the admin password header
        h = MagicMock()
        h.headers = {"X-Admin-Password": "correct_password"}
        
        assert _acad_auth_staff(h) is True
        mock_auth.assert_called_once_with("correct_password")


def test_acad_auth_staff_no_auth():
    """Test that missing auth credentials returns False."""
    h = MagicMock()
    h.headers = {}
    
    assert _acad_auth_staff(h) is False
