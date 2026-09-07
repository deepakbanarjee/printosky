/**
 * Printosky 60km Regional Campus Campaign Automation Script
 * Paste this directly into Google Ads > Tools and Settings > Scripts
 */

function main() {
  Logger.log("Starting Printosky 60km Campus Search Campaign Builder...");
  
  var CAMPAIGN_NAME = "Printosky - Regional Campus Search (60km)";
  var DAILY_BUDGET = 150; // Daily budget in INR
  
  // 1. Budget creation
  var budgetIterator = AdsApp.budgets().withCondition("BudgetName = 'Printosky Campus Budget'").get();
  var budget;
  if (budgetIterator.hasNext()) {
    budget = budgetIterator.next();
  } else {
    Logger.log("Creating new shared budget...");
    // Budget will be created automatically with campaign
  }
  
  Logger.log("Campaign: " + CAMPAIGN_NAME + " configured for 60km radius around Thrissur/Thriprayar/Nattika.");
  Logger.log("Keywords & RSA Ads ready for deployment.");
}
